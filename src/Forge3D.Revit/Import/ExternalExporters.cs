using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Forge3D.Core.Settings;

namespace Forge3D.Revit.Import;

/// <summary>
/// Exportação pelos programas de origem instalados na máquina:
/// - 3ds Max: 3dsmaxbatch.exe roda forge3d_export.ms sem abrir a interface;
/// - SketchUp (alternativa): SketchUp.exe com -RubyStartup forge3d_export.rb.
/// Os dois gravam OBJ em milímetros com Z para cima, um objeto por peça.
/// </summary>
internal static class ExternalExporters
{
    public static string ExportersDirectory =>
        Path.Combine(Path.GetDirectoryName(typeof(ExternalExporters).Assembly.Location)!, "exporters");

    public static string? Find3dsMaxBatch(Forge3DSettings settings)
    {
        if (!string.IsNullOrWhiteSpace(settings.Max3dsBatchPath) && File.Exists(settings.Max3dsBatchPath))
            return settings.Max3dsBatchPath;
        return FindLatest("3ds Max *", "3dsmaxbatch.exe");
    }

    public static string? FindSketchUp(Forge3DSettings settings)
    {
        if (!string.IsNullOrWhiteSpace(settings.SketchUpPath) && File.Exists(settings.SketchUpPath))
            return settings.SketchUpPath;
        var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "SketchUp");
        if (!Directory.Exists(root)) return null;
        return Directory.GetDirectories(root, "SketchUp *")
            .OrderByDescending(d => d)
            .Select(d => Path.Combine(d, "SketchUp", "SketchUp.exe"))
            .Concat(Directory.GetDirectories(root, "SketchUp *").Select(d => Path.Combine(d, "SketchUp.exe")))
            .FirstOrDefault(File.Exists);
    }

    private static string? FindLatest(string folderPattern, string exe)
    {
        var autodesk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Autodesk");
        if (!Directory.Exists(autodesk)) return null;
        return Directory.GetDirectories(autodesk, folderPattern)
            .OrderByDescending(d => d)
            .Select(d => Path.Combine(d, exe))
            .FirstOrDefault(File.Exists);
    }

    public static void ExportWith3dsMax(string batchExe, string sourcePath, string objPath, CancellationToken ct)
    {
        var script = Path.Combine(ExportersDirectory, "forge3d_export.ms");
        if (!File.Exists(script)) throw new FileNotFoundException("Script de exportação do 3ds Max não encontrado. Reinstale o Forge 3D.", script);
        var psi = new ProcessStartInfo(batchExe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add(script);
        psi.ArgumentList.Add("-mxsString");
        psi.ArgumentList.Add($"input:{sourcePath}");
        psi.ArgumentList.Add("-mxsString");
        psi.ArgumentList.Add($"output:{objPath}");
        RunAndCheck(psi, objPath, "3ds Max", TimeSpan.FromMinutes(15), ct);
    }

    public static void ExportWithSketchUp(string sketchUpExe, string sourcePath, string objPath, CancellationToken ct)
    {
        var script = Path.Combine(ExportersDirectory, "forge3d_export.rb");
        if (!File.Exists(script)) throw new FileNotFoundException("Script de exportação do SketchUp não encontrado. Reinstale o Forge 3D.", script);
        var job = Path.Combine(Path.GetDirectoryName(objPath)!, "forge3d_sketchup_job.json");
        File.WriteAllText(job, JsonSerializer.Serialize(new { input = sourcePath, output = objPath }), new UTF8Encoding(false));
        var psi = new ProcessStartInfo(sketchUpExe) { UseShellExecute = false };
        psi.ArgumentList.Add("-RubyStartup");
        psi.ArgumentList.Add(script);
        psi.Environment["FORGE3D_JOB"] = job;
        RunAndCheck(psi, objPath, "SketchUp", TimeSpan.FromMinutes(10), ct);
    }

    private static void RunAndCheck(ProcessStartInfo psi, string objPath, string program, TimeSpan timeout, CancellationToken ct)
    {
        if (File.Exists(objPath)) File.Delete(objPath);
        using var proc = Process.Start(psi) ?? throw new InvalidOperationException($"Não foi possível iniciar o {program}.");
        var output = new StringBuilder();
        if (psi.RedirectStandardOutput)
        {
            proc.OutputDataReceived += (_, e) => { if (e.Data is not null) lock (output) output.AppendLine(e.Data); };
            proc.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (output) output.AppendLine(e.Data); };
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();
        }
        var deadline = DateTime.UtcNow + timeout;
        while (!proc.WaitForExit(250))
        {
            if (ct.IsCancellationRequested || DateTime.UtcNow > deadline)
            {
                try { proc.Kill(entireProcessTree: true); } catch { /* já encerrado */ }
                ct.ThrowIfCancellationRequested();
                throw new TimeoutException($"O {program} não terminou a exportação no tempo limite.");
            }
            // SketchUp: o script cria o arquivo e fecha o programa; se o arquivo
            // já existe e está estável, não precisamos esperar mais.
            if (!psi.RedirectStandardOutput && File.Exists(objPath + ".ok")) break;
        }
        if (!File.Exists(objPath) || new FileInfo(objPath).Length < 32)
        {
            string tail;
            lock (output) tail = string.Join(Environment.NewLine, output.ToString().Split('\n').TakeLast(12));
            throw new InvalidOperationException($"O {program} não gerou o arquivo exportado.{Environment.NewLine}{tail}".Trim());
        }
    }
}
