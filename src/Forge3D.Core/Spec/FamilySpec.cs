using System.Text.Json;
using System.Text.Json.Serialization;

namespace Forge3D.Core.Spec;

/// <summary>
/// FamilySpec v1: contrato entre o motor (Python) e o plugin do Revit.
/// Comprimentos em milímetros, no sistema da família: X = largura,
/// Y = profundidade, Z = altura; origem no centro em planta e na base.
/// </summary>
public sealed class FamilySpec
{
    public const string SchemaName = "forge3d.familyspec";
    public const int SupportedVersion = 1;

    [JsonPropertyName("schema")] public string Schema { get; set; } = "";
    [JsonPropertyName("version")] public int Version { get; set; }
    [JsonPropertyName("generator")] public GeneratorInfo? Generator { get; set; }
    [JsonPropertyName("family")] public FamilyInfo Family { get; set; } = new();
    [JsonPropertyName("units")] public string Units { get; set; } = "mm";
    [JsonPropertyName("envelope")] public Envelope Envelope { get; set; } = new();
    [JsonPropertyName("parameters")] public List<ParameterSpec> Parameters { get; set; } = new();
    [JsonPropertyName("planes")] public List<PlaneSpec> Planes { get; set; } = new();
    [JsonPropertyName("materials")] public List<MaterialSpec> Materials { get; set; } = new();
    [JsonPropertyName("solids")] public List<SolidSpec> Solids { get; set; } = new();
    [JsonPropertyName("types")] public List<FamilyTypeSpec> Types { get; set; } = new();
    [JsonPropertyName("report")] public ReportSpec Report { get; set; } = new();

    public ParameterSpec? FindParameter(string? id) =>
        id is null ? null : Parameters.FirstOrDefault(p => p.Id == id);

    public PlaneSpec? FindPlane(string? id) =>
        id is null ? null : Planes.FirstOrDefault(p => p.Id == id);
}

public sealed class GeneratorInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("version")] public string Version { get; set; } = "";
}

public sealed class FamilyInfo
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "GenericModel";
    [JsonPropertyName("description")] public string? Description { get; set; }
    [JsonPropertyName("requestedLevel")] public string RequestedLevel { get; set; } = "C";
    [JsonPropertyName("achievedLevel")] public string AchievedLevel { get; set; } = "C";
}

public sealed class Envelope
{
    [JsonPropertyName("width")] public double Width { get; set; }
    [JsonPropertyName("depth")] public double Depth { get; set; }
    [JsonPropertyName("height")] public double Height { get; set; }
}

public static class ParameterKinds
{
    public const string Length = "length";
    public const string Angle = "angle";
    public const string Number = "number";
    public const string Integer = "integer";
    public const string YesNo = "yesno";
    public const string Material = "material";
    public const string Text = "text";

    public static bool IsNumeric(string kind) => kind is Length or Angle or Number or Integer;
}

public sealed class ParameterSpec
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = ParameterKinds.Length;
    [JsonPropertyName("group")] public string Group { get; set; } = "dimensions";
    [JsonPropertyName("instance")] public bool Instance { get; set; }
    [JsonPropertyName("value")] public JsonElement Value { get; set; }
    [JsonPropertyName("formula")] public string? Formula { get; set; }
    [JsonPropertyName("role")] public string Role { get; set; } = "";
    [JsonPropertyName("description")] public string? Description { get; set; }
    /// <summary>Faixa segura calculada pelo motor (mm): fora dela o Revit recusaria a geometria.</summary>
    [JsonPropertyName("min")] public double? Min { get; set; }
    [JsonPropertyName("max")] public double? Max { get; set; }

    /// <summary>Texto da faixa segura, ex.: "12 – 40", "≥ 12" ou "" (sem faixa calculada).</summary>
    public string SafeRangeText => (Min, Max) switch
    {
        ({ } lo, { } hi) => $"{lo:0.#} – {hi:0.#}",
        ({ } lo, null) => $"≥ {lo:0.#}",
        (null, { } hi) => $"≤ {hi:0.#}",
        _ => "",
    };

    /// <summary>O valor (mm) está dentro da faixa segura?</summary>
    public bool IsWithinSafeRange(double value) => (Min is not { } lo || value >= lo - 1e-6) && (Max is not { } hi || value <= hi + 1e-6);

    public bool HasFormula => !string.IsNullOrWhiteSpace(Formula);

    public double NumberValue => Value.ValueKind == JsonValueKind.Number ? Value.GetDouble() : 0.0;

    public string? StringValue => Value.ValueKind == JsonValueKind.String ? Value.GetString() : null;

    public bool BoolValue => Value.ValueKind == JsonValueKind.True ||
                             (Value.ValueKind == JsonValueKind.Number && Value.GetDouble() != 0);

    /// <summary>Parâmetro editável pelo usuário (envelope ou semântico, sem fórmula).</summary>
    public bool IsUserFacing => !HasFormula && Role is "envelope" or "semantic";
}

public sealed class PlaneSpec
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("axis")] public string Axis { get; set; } = "x";
    [JsonPropertyName("position")] public double Position { get; set; }
    [JsonPropertyName("parent")] public string Parent { get; set; } = "";
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("reference")] public string Reference { get; set; } = "none";
}

public sealed class AnchorSpec
{
    [JsonPropertyName("position")] public double Position { get; set; }
    [JsonPropertyName("parent")] public string Parent { get; set; } = "";
    [JsonPropertyName("driver")] public string? Driver { get; set; }
}

public sealed class CenterSpec
{
    [JsonPropertyName("u")] public AnchorSpec U { get; set; } = new();
    [JsonPropertyName("v")] public AnchorSpec V { get; set; } = new();
}

public sealed class DiameterSpec
{
    [JsonPropertyName("value")] public double Value { get; set; }
    [JsonPropertyName("driver")] public string? Driver { get; set; }
}

public sealed class SegmentSpec
{
    [JsonPropertyName("type")] public string Type { get; set; } = "line";
    [JsonPropertyName("start")] public double[] Start { get; set; } = new double[2];
    [JsonPropertyName("end")] public double[] End { get; set; } = new double[2];
    [JsonPropertyName("mid")] public double[]? Mid { get; set; }
    [JsonPropertyName("center")] public double[]? Center { get; set; }
    [JsonPropertyName("radius")] public double? Radius { get; set; }

    public bool IsArc => Type == "arc";
}

public sealed class AlignmentSpec
{
    [JsonPropertyName("loop")] public int Loop { get; set; }
    [JsonPropertyName("segment")] public int Segment { get; set; }
    [JsonPropertyName("plane")] public string Plane { get; set; } = "";
}

public sealed class FrameSpec
{
    [JsonPropertyName("origin")] public double[] Origin { get; set; } = new double[3];
    [JsonPropertyName("xdir")] public double[] XDir { get; set; } = new double[3];
    [JsonPropertyName("ydir")] public double[] YDir { get; set; } = new double[3];
}

public static class SolidKinds
{
    public const string Box = "box";
    public const string Cylinder = "cylinder";
    public const string Extrusion = "extrusion";
    public const string Revolution = "revolution";
    public const string Mesh = "mesh";
}

public sealed class SolidSpec
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("material")] public string? Material { get; set; }
    [JsonPropertyName("isVoid")] public bool IsVoid { get; set; }
    [JsonPropertyName("visibility")] public string Visibility { get; set; } = "all";
    [JsonPropertyName("part")] public string Part { get; set; } = "";

    // caixa
    [JsonPropertyName("x")] public string[]? X { get; set; }
    [JsonPropertyName("y")] public string[]? Y { get; set; }
    [JsonPropertyName("z")] public string[]? Z { get; set; }

    // cilindro, extrusão alinhada e revolução
    [JsonPropertyName("axis")] public string? Axis { get; set; }
    [JsonPropertyName("center")] public CenterSpec? Center { get; set; }
    [JsonPropertyName("diameter")] public DiameterSpec? Diameter { get; set; }
    [JsonPropertyName("extent")] public string[]? Extent { get; set; }

    // extrusão
    [JsonPropertyName("loops")] public List<List<SegmentSpec>>? Loops { get; set; }
    [JsonPropertyName("alignments")] public List<AlignmentSpec>? Alignments { get; set; }
    [JsonPropertyName("frame")] public FrameSpec? Frame { get; set; }
    [JsonPropertyName("depth")] public double? Depth { get; set; }

    // revolução
    [JsonPropertyName("profile")] public List<SegmentSpec>? Profile { get; set; }
    [JsonPropertyName("range")] public double[]? Range { get; set; }

    // malha
    [JsonPropertyName("vertices")] public List<double[]>? Vertices { get; set; }
    [JsonPropertyName("faces")] public List<int[]>? Faces { get; set; }

    public bool IsFramedExtrusion => Kind == SolidKinds.Extrusion && Frame is not null;
}

public sealed class MaterialSpec
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("color")] public int[] Color { get; set; } = { 180, 180, 180 };
    [JsonPropertyName("transparency")] public double? Transparency { get; set; }
    [JsonPropertyName("shininess")] public int? Shininess { get; set; }
    [JsonPropertyName("smoothness")] public int? Smoothness { get; set; }
}

public sealed class FamilyTypeSpec
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("values")] public Dictionary<string, JsonElement> Values { get; set; } = new();
}

public sealed class PartReport
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("source")] public string? Source { get; set; }
    [JsonPropertyName("kind")] public string Kind { get; set; } = "";
    [JsonPropertyName("level")] public string Level { get; set; } = "";
    [JsonPropertyName("volume")] public double Volume { get; set; }
    [JsonPropertyName("deviation")] public double Deviation { get; set; }
    [JsonPropertyName("detail")] public bool Detail { get; set; }
    [JsonPropertyName("notes")] public List<string> Notes { get; set; } = new();
}

public sealed class FlexReport
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("failures")] public List<string> Failures { get; set; } = new();
    [JsonPropertyName("checked")] public List<string> Checked { get; set; } = new();
}

public sealed class ReportSpec
{
    [JsonPropertyName("requestedLevel")] public string RequestedLevel { get; set; } = "C";
    [JsonPropertyName("achievedLevel")] public string AchievedLevel { get; set; } = "C";
    [JsonPropertyName("parametricCoverage")] public double ParametricCoverage { get; set; }
    [JsonPropertyName("parts")] public List<PartReport> Parts { get; set; } = new();
    [JsonPropertyName("warnings")] public List<string> Warnings { get; set; } = new();
    [JsonPropertyName("levelCBlockers")] public List<string> LevelCBlockers { get; set; } = new();
    [JsonPropertyName("flex")] public FlexReport? Flex { get; set; }
    [JsonPropertyName("fidelity")] public JsonElement? Fidelity { get; set; }
    [JsonPropertyName("source")] public JsonElement? Source { get; set; }
    [JsonPropertyName("image")] public JsonElement? Image { get; set; }
    [JsonPropertyName("timings")] public JsonElement? Timings { get; set; }

    [JsonExtensionData] public Dictionary<string, JsonElement>? Extra { get; set; }
}
