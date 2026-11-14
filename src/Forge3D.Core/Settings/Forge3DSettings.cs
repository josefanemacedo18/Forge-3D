using System.Text.Json;
using System.Text.Json.Serialization;

namespace Forge3D.Core.Settings;

/// <summary>
/// Configurações do usuário, em %APPDATA%\Forge3D\config.json.
/// Campos vazios usam a detecção automática.
/// </summary>
public sealed class Forge3DSettings
{
    [JsonPropertyName("pythonPath")] public string PythonPath { get; set; } = "";
    [JsonPropertyName("engineDirectory")] public string EngineDirectory { get; set; } = "";
    [JsonPropertyName("modelsDirectory")] public string ModelsDirectory { get; set; } = "";
    [JsonPropertyName("familyTemplatePath")] public string FamilyTemplatePath { get; set; } = "";
    [JsonPropertyName("outputDirectory")] public string OutputDirectory { get; set; } = "";
    [JsonPropertyName("max3dsBatchPath")] public string Max3dsBatchPath { get; set; } = "";
    [JsonPropertyName("sketchUpPath")] public string SketchUpPath { get; set; } = "";
    [JsonPropertyName("defaultLevel")] public string DefaultLevel { get; set; } = "C";
    [JsonPropertyName("defaultCategory")] public string DefaultCategory { get; set; } = "Furniture";
    [JsonPropertyName("askBeforeLevelB")] public bool AskBeforeLevelB { get; set; } = true;
    [JsonPropertyName("loadIntoProject")] public bool LoadIntoProject { get; set; } = true;
    [JsonPropertyName("placeAfterLoad")] public bool PlaceAfterLoad { get; set; } = true;
    [JsonPropertyName("runFlexTest")] public bool RunFlexTest { get; set; } = true;
    /// <summary>Inicia o motor em segundo plano ao abrir o Revit (a primeira conversão já sai na hora).</summary>
    [JsonPropertyName("preloadEngine")] public bool PreloadEngine { get; set; } = true;
    [JsonPropertyName("fitTolerance")] public double FitTolerance { get; set; } = 1.0;
    [JsonPropertyName("maxFreeformFaces")] public int MaxFreeformFaces { get; set; } = 15000;
    [JsonPropertyName("useAiBackgroundRemoval")] public bool UseAiBackgroundRemoval { get; set; } = true;

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static string ConfigDirectory =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Forge3D");

    public static string ConfigPath => Path.Combine(ConfigDirectory, "config.json");

    public static string LocalDataDirectory =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Forge3D");

    public static string LogDirectory => Path.Combine(LocalDataDirectory, "logs");

    public string ResolvedOutputDirectory => string.IsNullOrWhiteSpace(OutputDirectory)
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Forge3D", "Familias")
        : Environment.ExpandEnvironmentVariables(OutputDirectory);

    public string ResolvedModelsDirectory => string.IsNullOrWhiteSpace(ModelsDirectory)
        ? Path.Combine(LocalDataDirectory, "models")
        : Environment.ExpandEnvironmentVariables(ModelsDirectory);

    public static Forge3DSettings Load()
    {
        try
        {
            if (File.Exists(ConfigPath))
                return JsonSerializer.Deserialize<Forge3DSettings>(File.ReadAllText(ConfigPath), Options) ?? new Forge3DSettings();
        }
        catch (Exception)
        {
            // Arquivo corrompido: guarda uma cópia e volta ao padrão.
            try { File.Copy(ConfigPath, ConfigPath + ".corrompido", true); } catch { /* sem cópia */ }
        }
        return new Forge3DSettings();
    }

    public void Save()
    {
        Directory.CreateDirectory(ConfigDirectory);
        var tmp = ConfigPath + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(this, Options));
        File.Move(tmp, ConfigPath, overwrite: true);
    }
}
