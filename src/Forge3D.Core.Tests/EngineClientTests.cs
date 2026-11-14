using Forge3D.Core.Engine;
using Forge3D.Core.Spec;
using Xunit;

namespace Forge3D.Core.Tests;

/// <summary>
/// Testes de integração com o motor real. Rodam quando FORGE3D_TEST_ENGINE
/// aponta para a pasta "engine" com o ambiente Python criado (.venv).
/// </summary>
public class EngineClientTests
{
    private static EngineStartInfo? Info()
    {
        var dir = Environment.GetEnvironmentVariable("FORGE3D_TEST_ENGINE");
        if (string.IsNullOrEmpty(dir)) return null;
        var py = OperatingSystem.IsWindows() ? Path.Combine(dir, ".venv", "Scripts", "python.exe") : Path.Combine(dir, ".venv", "bin", "python");
        return new EngineStartInfo { PythonPath = py, EngineDirectory = dir, LogDirectory = Path.Combine(Path.GetTempPath(), "forge3d-test-logs") };
    }

    [Fact]
    public async Task Converts_obj_through_the_real_engine()
    {
        var info = Info();
        if (info is null) return; // ambiente sem motor: teste ignorado
        var tmp = Directory.CreateTempSubdirectory("forge3d").FullName;
        var obj = Path.Combine(tmp, "caixa.obj");
        var part = new Forge3D.Core.Mesh.MeshPart { Name = "caixa" };
        double[][] v = { new[] { 0d, 0, 0 }, new[] { 400d, 0, 0 }, new[] { 400d, 300, 0 }, new[] { 0d, 300, 0 },
                         new[] { 0d, 0, 200 }, new[] { 400d, 0, 200 }, new[] { 400d, 300, 200 }, new[] { 0d, 300, 200 } };
        foreach (var p in v) part.Vertices.Add((p[0], p[1], p[2]));
        int[][] f = { new[] { 0, 2, 1 }, new[] { 0, 3, 2 }, new[] { 4, 5, 6 }, new[] { 4, 6, 7 }, new[] { 0, 1, 5 }, new[] { 0, 5, 4 },
                      new[] { 1, 2, 6 }, new[] { 1, 6, 5 }, new[] { 2, 3, 7 }, new[] { 2, 7, 6 }, new[] { 3, 0, 4 }, new[] { 3, 4, 7 } };
        foreach (var t in f) part.Triangles.Add((t[0], t[1], t[2]));
        Forge3D.Core.Mesh.ObjWriter.Write(obj, new[] { part });

        using var client = new EngineClient(info);
        await client.EnsureStartedAsync();
        var ping = await client.PingAsync();
        Assert.Equal(EngineApi.ProtocolVersion, ping.Protocol);
        var progress = new List<EngineProgress>();
        var result = await client.ConvertMeshAsync(new ConvertMeshRequest { Path = obj, Output = Path.Combine(tmp, "caixa.json"), Level = "C" },
            new SyncProgress(progress.Add), CancellationToken.None);
        Assert.Equal("C", result.Summary.AchievedLevel);
        Assert.NotEmpty(progress);
        var spec = SpecLoader.LoadValidated(result.SpecPath);
        Assert.Equal(400, spec.Envelope.Width, 3);

        // Tamanho real informado: a escala é corrigida pelo motor (e o motor já
        // está livre para o pedido seguinte assim que a resposta chega).
        var scaled = await client.ConvertMeshAsync(new ConvertMeshRequest
        {
            Path = obj, Output = Path.Combine(tmp, "caixa_escala.json"), Level = "C", TargetSize = 1000, TargetAxis = "z",
        }, null, CancellationToken.None);
        var scaledSpec = SpecLoader.LoadValidated(scaled.SpecPath);
        Assert.Equal(1000, scaledSpec.Envelope.Height, 3);
        Assert.Equal(2000, scaledSpec.Envelope.Width, 3);

        var ex = await Assert.ThrowsAsync<EngineException>(() => client.ConvertMeshAsync(
            new ConvertMeshRequest { Path = Path.Combine(tmp, "nao_existe.obj"), Output = Path.Combine(tmp, "x.json") }, null, CancellationToken.None));
        Assert.Equal(EngineException.CodeEngine, ex.Code);
    }

    [Fact]
    public async Task Missing_python_gives_clear_message()
    {
        using var client = new EngineClient(new EngineStartInfo { PythonPath = "/nao/existe/python", EngineDirectory = "/nao/existe" });
        var ex = await Assert.ThrowsAsync<EngineException>(() => client.EnsureStartedAsync());
        Assert.Contains("instalador", ex.Message);
    }

    private sealed class SyncProgress(Action<EngineProgress> action) : IProgress<EngineProgress>
    {
        public void Report(EngineProgress value) { lock (this) action(value); }
    }
}
