using Forge3D.Core.Settings;

namespace Forge3D.Core.Engine;

/// <summary>
/// Localiza o Python e a pasta do motor. Ordem: configuração do usuário,
/// instalação padrão (%LOCALAPPDATA%\Forge3D\engine) e pasta "engine" ao lado
/// do plugin (útil em desenvolvimento).
/// </summary>
public static class EngineLocator
{
    public sealed record Location(string PythonPath, string EngineDirectory, string ModelsDirectory, List<string> Checked);

    public static Location Resolve(Forge3DSettings settings, string? addinDirectory = null)
    {
        var checkedPaths = new List<string>();
        var candidates = new List<string>();
        if (!string.IsNullOrWhiteSpace(settings.EngineDirectory))
            candidates.Add(Environment.ExpandEnvironmentVariables(settings.EngineDirectory));
        candidates.Add(Path.Combine(Forge3DSettings.LocalDataDirectory, "engine"));
        if (addinDirectory is not null)
        {
            candidates.Add(Path.Combine(addinDirectory, "engine"));
            var dir = new DirectoryInfo(addinDirectory);
            for (int i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
                candidates.Add(Path.Combine(dir.FullName, "engine"));
        }

        string? engineDir = null;
        foreach (var c in candidates)
        {
            checkedPaths.Add(c);
            if (Directory.Exists(Path.Combine(c, "forge3d")))
            {
                engineDir = c;
                break;
            }
        }
        engineDir ??= candidates[0];

        // Python: o configurado (se existir), o Python embutido no pacote
        // (engine\python) ou um ambiente de desenvolvimento (.venv).
        var pythons = new List<string>();
        if (!string.IsNullOrWhiteSpace(settings.PythonPath))
            pythons.Add(Environment.ExpandEnvironmentVariables(settings.PythonPath));
        pythons.AddRange(PythonCandidates(engineDir));
        string python = pythons[0];
        foreach (var p in pythons)
        {
            checkedPaths.Add(p);
            if (File.Exists(p))
            {
                python = p;
                break;
            }
        }
        return new Location(python, engineDir, settings.ResolvedModelsDirectory, checkedPaths);
    }

    /// <summary>Onde procurar o Python dentro da pasta do motor.</summary>
    public static IEnumerable<string> PythonCandidates(string engineDir)
    {
        if (OperatingSystem.IsWindows())
        {
            yield return Path.Combine(engineDir, "python", "python.exe");
            yield return Path.Combine(engineDir, ".venv", "Scripts", "python.exe");
        }
        else
        {
            yield return Path.Combine(engineDir, ".venv", "bin", "python");
        }
    }

    public static EngineStartInfo StartInfo(Forge3DSettings settings, string? addinDirectory = null)
    {
        var loc = Resolve(settings, addinDirectory);
        return new EngineStartInfo
        {
            PythonPath = loc.PythonPath,
            EngineDirectory = loc.EngineDirectory,
            ModelsDirectory = loc.ModelsDirectory,
            LogDirectory = Forge3DSettings.LogDirectory,
        };
    }
}
