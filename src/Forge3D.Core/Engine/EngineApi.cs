using System.Text.Json;
using System.Text.Json.Serialization;

namespace Forge3D.Core.Engine;

/// <summary>Pedido de conversão de arquivo 3D (método convert_mesh).</summary>
public sealed class ConvertMeshRequest
{
    [JsonPropertyName("path")] public required string Path { get; init; }
    [JsonPropertyName("output")] public required string Output { get; init; }
    [JsonPropertyName("level")] public string Level { get; init; } = "C";
    [JsonPropertyName("units")] public string Units { get; init; } = "auto";
    [JsonPropertyName("upAxis")] public string UpAxis { get; init; } = "auto";
    [JsonPropertyName("category")] public string Category { get; init; } = "GenericModel";
    [JsonPropertyName("familyName")] public string? FamilyName { get; init; }
    [JsonPropertyName("fitTol")] public double FitTolerance { get; init; } = 1.0;
    [JsonPropertyName("allowApprox")] public bool AllowApproximation { get; init; } = true;
    [JsonPropertyName("sheetThickness")] public double SheetThickness { get; init; } = 3.0;
    [JsonPropertyName("maxFreeformFaces")] public int MaxFreeformFaces { get; init; } = 15000;
    [JsonPropertyName("computeFidelity")] public bool ComputeFidelity { get; init; } = true;
    /// <summary>Medida real conhecida do objeto (mm); nula = usar a unidade do arquivo.</summary>
    [JsonPropertyName("targetSize")] public double? TargetSize { get; init; }
    /// <summary>Qual medida é o <see cref="TargetSize"/>: z (altura), x (largura), y (profundidade) ou max.</summary>
    [JsonPropertyName("targetAxis")] public string TargetAxis { get; init; } = "z";
}

/// <summary>Pedido de conversão de imagem (método convert_image).</summary>
public sealed class ConvertImageRequest
{
    [JsonPropertyName("path")] public required string Path { get; init; }
    [JsonPropertyName("output")] public required string Output { get; init; }
    [JsonPropertyName("strategy")] public string Strategy { get; init; } = "auto";
    [JsonPropertyName("reference")] public string Reference { get; init; } = "altura";
    [JsonPropertyName("referenceMm")] public double? ReferenceMm { get; init; }
    [JsonPropertyName("depthMm")] public double? DepthMm { get; init; }
    [JsonPropertyName("level")] public string Level { get; init; } = "C";
    [JsonPropertyName("category")] public string Category { get; init; } = "GenericModel";
    [JsonPropertyName("familyName")] public string? FamilyName { get; init; }
    [JsonPropertyName("useAiMask")] public bool UseAiMask { get; init; } = true;
    [JsonPropertyName("workDir")] public string? WorkDirectory { get; init; }
}

public sealed class AnalyzeImageRequest
{
    [JsonPropertyName("path")] public required string Path { get; init; }
    [JsonPropertyName("workDir")] public string? WorkDirectory { get; init; }
    [JsonPropertyName("useAiMask")] public bool UseAiMask { get; init; } = true;
}

public sealed class EnvelopeDto
{
    [JsonPropertyName("width")] public double Width { get; set; }
    [JsonPropertyName("depth")] public double Depth { get; set; }
    [JsonPropertyName("height")] public double Height { get; set; }
}

public sealed class ConversionSummary
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("requestedLevel")] public string RequestedLevel { get; set; } = "";
    [JsonPropertyName("achievedLevel")] public string AchievedLevel { get; set; } = "";
    [JsonPropertyName("parts")] public int Parts { get; set; }
    [JsonPropertyName("planes")] public int Planes { get; set; }
    [JsonPropertyName("parameters")] public int Parameters { get; set; }
    [JsonPropertyName("envelope")] public EnvelopeDto Envelope { get; set; } = new();
    [JsonPropertyName("coverage")] public double Coverage { get; set; }
    [JsonPropertyName("levelCBlockers")] public List<string> LevelCBlockers { get; set; } = new();
    [JsonPropertyName("warnings")] public List<string> Warnings { get; set; } = new();
    [JsonPropertyName("strategy")] public string? Strategy { get; set; }
}

public sealed class ConversionResult
{
    [JsonPropertyName("specPath")] public string SpecPath { get; set; } = "";
    [JsonPropertyName("previewPath")] public string? PreviewPath { get; set; }
    [JsonPropertyName("summary")] public ConversionSummary Summary { get; set; } = new();
}

public sealed class ImageAnalysis
{
    [JsonPropertyName("silhouetteMethod")] public string SilhouetteMethod { get; set; } = "";
    [JsonPropertyName("symmetry")] public double Symmetry { get; set; }
    [JsonPropertyName("rowFill")] public double RowFill { get; set; }
    [JsonPropertyName("aspect")] public double Aspect { get; set; }
    [JsonPropertyName("suggestedStrategy")] public string SuggestedStrategy { get; set; } = "";
    [JsonPropertyName("reason")] public string Reason { get; set; } = "";
    [JsonPropertyName("preview")] public string Preview { get; set; } = "";
    [JsonPropertyName("ai")] public JsonElement Ai { get; set; }
}

public sealed class PingResult
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("protocol")] public int Protocol { get; set; }
    [JsonPropertyName("python")] public string Python { get; set; } = "";
    [JsonPropertyName("busy")] public bool Busy { get; set; }
}

/// <summary>Atalhos tipados para os métodos do motor.</summary>
public static class EngineApi
{
    public const int ProtocolVersion = 1;

    public static Task<PingResult> PingAsync(this EngineClient c, CancellationToken ct = default) =>
        c.CallAsync<PingResult>("ping", null, null, ct, TimeSpan.FromSeconds(30));

    public static Task<ConversionResult> ConvertMeshAsync(this EngineClient c, ConvertMeshRequest r,
        IProgress<EngineProgress>? progress, CancellationToken ct) =>
        c.CallAsync<ConversionResult>("convert_mesh", r, progress, ct);

    public static Task<ConversionResult> ConvertImageAsync(this EngineClient c, ConvertImageRequest r,
        IProgress<EngineProgress>? progress, CancellationToken ct) =>
        c.CallAsync<ConversionResult>("convert_image", r, progress, ct);

    public static Task<ImageAnalysis> AnalyzeImageAsync(this EngineClient c, AnalyzeImageRequest r, CancellationToken ct) =>
        c.CallAsync<ImageAnalysis>("analyze_image", r, null, ct, TimeSpan.FromMinutes(5));

    public static Task<JsonElement> HardwareAsync(this EngineClient c, CancellationToken ct = default) =>
        c.CallAsync("hardware", null, null, ct, TimeSpan.FromMinutes(2));
}
