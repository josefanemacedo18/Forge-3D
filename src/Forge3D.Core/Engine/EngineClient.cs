using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Forge3D.Core.Engine;

public sealed record EngineProgress(double Fraction, string Message);

/// <summary>Erro informado pelo motor (mensagem já em português).</summary>
public sealed class EngineException : Exception
{
    public const int CodeEngine = 1, CodeInternal = 2, CodeCancelled = 3, CodeBusy = 4, CodeProcess = 100;

    public int Code { get; }
    public IReadOnlyList<string> Details { get; }

    public EngineException(int code, string message, IReadOnlyList<string>? details = null, Exception? inner = null)
        : base(message, inner)
    {
        Code = code;
        Details = details ?? Array.Empty<string>();
    }
}

public sealed class EngineStartInfo
{
    public required string PythonPath { get; init; }
    public required string EngineDirectory { get; init; }
    public string? LogDirectory { get; init; }
    public string? ModelsDirectory { get; init; }
    public TimeSpan StartTimeout { get; init; } = TimeSpan.FromSeconds(60);
}

/// <summary>
/// Cliente do motor Forge 3D: processo Python local falando JSON-RPC 2.0
/// pela entrada/saída padrão (uma mensagem por linha, UTF-8 sem BOM).
/// Seguro para uso de várias threads; uma conversão por vez no motor.
/// </summary>
public sealed class EngineClient : IDisposable
{
    private sealed class Pending
    {
        public required TaskCompletionSource<JsonElement> Completion { get; init; }
        public IProgress<EngineProgress>? Progress { get; init; }
    }

    private readonly EngineStartInfo _info;
    private readonly ConcurrentDictionary<long, Pending> _pending = new();
    private readonly object _writeLock = new();
    private readonly LinkedList<string> _stderrTail = new();
    private Process? _process;
    private long _nextId;
    private bool _disposed;
    // Um único início por vez: o pré-carregamento ao abrir o Revit e um clique
    // do usuário nunca criam dois processos; quem chega depois espera o motor
    // ficar pronto.
    private readonly SemaphoreSlim _startGate = new(1, 1);
    private volatile bool _ready;

    public EngineClient(EngineStartInfo info) => _info = info;

    public bool IsRunning => _process is { HasExited: false };

    /// <summary>Últimas linhas de erro do processo (para diagnóstico).</summary>
    public string StderrTail
    {
        get { lock (_stderrTail) return string.Join(Environment.NewLine, _stderrTail); }
    }

    /// <summary>O processo está rodando e já respondeu ao primeiro ping.</summary>
    public bool IsReady => _ready && IsRunning;

    public async Task EnsureStartedAsync(CancellationToken ct = default)
    {
        if (IsReady) return;
        await _startGate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            if (IsReady) return;
            if (!IsRunning)
            {
                _ready = false;
                Start();
            }
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
            cts.CancelAfter(_info.StartTimeout);
            try
            {
                await CallCoreAsync("ping", null, null, cts.Token, null).ConfigureAwait(false);
                _ready = true;
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                Kill();
                throw new EngineException(EngineException.CodeProcess,
                    "O motor do Forge 3D não respondeu ao iniciar. Verifique a instalação (Configurações > Diagnóstico).",
                    SplitLines(StderrTail));
            }
        }
        finally
        {
            _startGate.Release();
        }
    }

    private void Start()
    {
        if (!File.Exists(_info.PythonPath))
            throw new EngineException(EngineException.CodeProcess,
                $"Python do motor não encontrado em '{_info.PythonPath}'. Execute o instalador do Forge 3D.");
        if (!Directory.Exists(Path.Combine(_info.EngineDirectory, "forge3d")))
            throw new EngineException(EngineException.CodeProcess,
                $"Motor do Forge 3D não encontrado em '{_info.EngineDirectory}'. Execute o instalador do Forge 3D.");

        var psi = new ProcessStartInfo(_info.PythonPath)
        {
            WorkingDirectory = _info.EngineDirectory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
            StandardInputEncoding = new UTF8Encoding(false),
        };
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("forge3d");
        psi.ArgumentList.Add("servidor");
        if (!string.IsNullOrEmpty(_info.LogDirectory))
        {
            psi.ArgumentList.Add("--logs");
            psi.ArgumentList.Add(_info.LogDirectory);
        }
        psi.Environment["PYTHONPATH"] = _info.EngineDirectory;
        psi.Environment["PYTHONUTF8"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        psi.Environment["PYTHONNOUSERSITE"] = "1"; // isolado: pacotes "pip --user" do usuário não interferem
        if (!string.IsNullOrEmpty(_info.ModelsDirectory))
        {
            psi.Environment["FORGE3D_MODELS"] = _info.ModelsDirectory;
            psi.Environment["U2NET_HOME"] = Path.Combine(_info.ModelsDirectory, "u2net");
        }

        var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
        proc.Exited += (_, _) => FailAll(new EngineException(EngineException.CodeProcess,
            "O motor do Forge 3D foi encerrado inesperadamente.", SplitLines(StderrTail)));
        if (!proc.Start())
            throw new EngineException(EngineException.CodeProcess, "Não foi possível iniciar o motor do Forge 3D.");
        _process = proc;
        _ = Task.Run(() => ReadLoop(proc));
        _ = Task.Run(() => StderrLoop(proc));
    }

    private void ReadLoop(Process proc)
    {
        try
        {
            string? line;
            while ((line = proc.StandardOutput.ReadLine()) is not null)
                HandleLine(line);
        }
        catch (Exception ex)
        {
            FailAll(new EngineException(EngineException.CodeProcess, "Falha na comunicação com o motor.", null, ex));
        }
    }

    private void StderrLoop(Process proc)
    {
        try
        {
            string? line;
            while ((line = proc.StandardError.ReadLine()) is not null)
            {
                lock (_stderrTail)
                {
                    _stderrTail.AddLast(line);
                    while (_stderrTail.Count > 60) _stderrTail.RemoveFirst();
                }
            }
        }
        catch
        {
            // stderr é apenas diagnóstico
        }
    }

    internal void HandleLine(string line)
    {
        if (string.IsNullOrWhiteSpace(line)) return;
        JsonElement msg;
        try
        {
            using var doc = JsonDocument.Parse(line);
            msg = doc.RootElement.Clone();
        }
        catch (JsonException)
        {
            return; // linha que não é do protocolo
        }
        if (msg.TryGetProperty("method", out var m) && m.GetString() == "progress")
        {
            if (msg.TryGetProperty("params", out var p) && p.TryGetProperty("id", out var pid) &&
                pid.ValueKind == JsonValueKind.Number && _pending.TryGetValue(pid.GetInt64(), out var pend))
            {
                var fraction = p.TryGetProperty("fraction", out var f) ? f.GetDouble() : 0;
                var text = p.TryGetProperty("message", out var t) ? t.GetString() ?? "" : "";
                pend.Progress?.Report(new EngineProgress(fraction, text));
            }
            return;
        }
        if (!msg.TryGetProperty("id", out var idEl) || idEl.ValueKind != JsonValueKind.Number) return;
        if (!_pending.TryRemove(idEl.GetInt64(), out var pending)) return;
        if (msg.TryGetProperty("error", out var err))
        {
            int code = err.TryGetProperty("code", out var c) ? c.GetInt32() : EngineException.CodeInternal;
            string message = err.TryGetProperty("message", out var em) ? em.GetString() ?? "" : "Erro do motor.";
            var details = new List<string>();
            if (err.TryGetProperty("data", out var data))
            {
                if (data.TryGetProperty("errors", out var errs) && errs.ValueKind == JsonValueKind.Array)
                    details.AddRange(errs.EnumerateArray().Select(e => e.GetString() ?? ""));
                if (data.TryGetProperty("trace", out var tr)) details.Add(tr.GetString() ?? "");
            }
            pending.Completion.TrySetException(new EngineException(code, message, details));
            return;
        }
        pending.Completion.TrySetResult(msg.TryGetProperty("result", out var r) ? r : default);
    }

    private void FailAll(Exception ex)
    {
        foreach (var key in _pending.Keys.ToList())
            if (_pending.TryRemove(key, out var p)) p.Completion.TrySetException(ex);
    }

    private void Send(JsonObject msg)
    {
        var proc = _process ?? throw new EngineException(EngineException.CodeProcess, "O motor não está em execução.");
        var line = msg.ToJsonString();
        lock (_writeLock)
        {
            proc.StandardInput.WriteLine(line);
            proc.StandardInput.Flush();
        }
    }

    /// <summary>
    /// Chama um método do motor. Cancelar o token pede o cancelamento ao motor
    /// e aguarda a confirmação (até 15 s) antes de lançar OperationCanceledException.
    /// </summary>
    public async Task<JsonElement> CallAsync(string method, object? parameters, IProgress<EngineProgress>? progress,
        CancellationToken ct = default, TimeSpan? timeout = null)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        await EnsureStartedAsync(ct).ConfigureAwait(false); // espera o motor ficar pronto (ou o inicia)
        return await CallCoreAsync(method, parameters, progress, ct, timeout).ConfigureAwait(false);
    }

    private async Task<JsonElement> CallCoreAsync(string method, object? parameters, IProgress<EngineProgress>? progress,
        CancellationToken ct, TimeSpan? timeout)
    {
        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = new Pending { Completion = tcs, Progress = progress };
        var msg = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["method"] = method,
            ["params"] = parameters is null ? new JsonObject() : JsonSerializer.SerializeToNode(parameters),
        };
        try
        {
            Send(msg);
        }
        catch (Exception ex) when (ex is not EngineException)
        {
            _pending.TryRemove(id, out _);
            throw new EngineException(EngineException.CodeProcess, "Não foi possível enviar o pedido ao motor.", null, ex);
        }

        using var timeoutCts = timeout is null ? null : new CancellationTokenSource(timeout.Value);
        var waitTask = tcs.Task;
        var cancelTask = Task.Delay(Timeout.Infinite, ct);
        var timeoutTask = timeoutCts is null ? Task.Delay(Timeout.Infinite) : Task.Delay(Timeout.Infinite, timeoutCts.Token);
        var first = await Task.WhenAny(waitTask, cancelTask, timeoutTask).ConfigureAwait(false);
        if (first == waitTask) return await waitTask.ConfigureAwait(false);

        // Cancelamento ou tempo esgotado: pede ao motor para parar e espera a confirmação.
        try
        {
            Send(new JsonObject { ["jsonrpc"] = "2.0", ["method"] = "cancel", ["params"] = new JsonObject { ["id"] = id } });
        }
        catch
        {
            // processo pode já ter terminado
        }
        await Task.WhenAny(waitTask, Task.Delay(TimeSpan.FromSeconds(15))).ConfigureAwait(false);
        _pending.TryRemove(id, out _);
        if (first == timeoutTask)
            throw new EngineException(EngineException.CodeProcess, $"O motor não respondeu a '{method}' no tempo limite.");
        throw new OperationCanceledException(ct);
    }

    public async Task<T> CallAsync<T>(string method, object? parameters, IProgress<EngineProgress>? progress = null,
        CancellationToken ct = default, TimeSpan? timeout = null)
    {
        var result = await CallAsync(method, parameters, progress, ct, timeout).ConfigureAwait(false);
        return result.Deserialize<T>(Forge3D.Core.Spec.SpecLoader.JsonOptions)
               ?? throw new EngineException(EngineException.CodeInternal, $"Resposta vazia do motor para '{method}'.");
    }

    private static List<string> SplitLines(string text) =>
        text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).TakeLast(15).ToList();

    private void Kill()
    {
        try
        {
            if (_process is { HasExited: false } p) p.Kill(entireProcessTree: true);
        }
        catch
        {
            // já encerrado
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        try
        {
            if (IsRunning)
            {
                Send(new JsonObject { ["jsonrpc"] = "2.0", ["id"] = Interlocked.Increment(ref _nextId), ["method"] = "shutdown" });
                _process!.WaitForExit(3000);
            }
        }
        catch
        {
            // encerramento forçado abaixo
        }
        Kill();
        _process?.Dispose();
        FailAll(new ObjectDisposedException(nameof(EngineClient)));
    }
}
