using System.Text;
using System.Text.Json;
using Forge3D.Core.Spec;

namespace Forge3D.Revit.Building;

/// <summary>Resultado de cada etapa da construção da família no Revit.</summary>
public sealed class BuildReport
{
    public sealed record Item(string Stage, string Subject, bool Ok, string Detail);

    public string FamilyName { get; }
    public string RequestedLevel { get; }
    public string EngineLevel { get; }
    public List<Item> Items { get; } = new();
    public List<string> Warnings { get; } = new();
    public List<string> FlexResults { get; } = new();
    public bool FlexOk { get; set; } = true;
    public bool EnvelopeOk { get; set; } = true;
    public string? EnvelopeDetail { get; set; }
    public string? FamilyPath { get; set; }
    public TimeSpan Duration { get; set; }

    public BuildReport(FamilySpec spec)
    {
        FamilyName = spec.Family.Name;
        RequestedLevel = spec.Family.RequestedLevel;
        EngineLevel = spec.Family.AchievedLevel;
    }

    public void Ok(string stage, string subject, string detail = "") => Items.Add(new Item(stage, subject, true, detail));

    public void Fail(string stage, string subject, string detail) => Items.Add(new Item(stage, subject, false, detail));

    public int Failures(string? stage = null) => Items.Count(i => !i.Ok && (stage is null || i.Stage == stage));

    public int Successes(string? stage = null) => Items.Count(i => i.Ok && (stage is null || i.Stage == stage));

    /// <summary>
    /// Nível efetivo depois da construção: se alguma restrição falhou no Revit,
    /// a família pode ficar abaixo do nível previsto pelo motor.
    /// </summary>
    public string EffectiveLevel
    {
        get
        {
            if (EngineLevel == "A") return "A";
            bool constraintsFailed = Failures("Restrições") > 0 || Failures("Cotas") > 0 || Failures("Fórmulas") > 0;
            if (EngineLevel == "C" && (constraintsFailed || !FlexOk)) return "B";
            return EngineLevel;
        }
    }

    public string ToText()
    {
        var sb = new StringBuilder();
        sb.AppendLine($"Família: {FamilyName}");
        sb.AppendLine($"Nível pedido: {RequestedLevel}   previsto pelo motor: {EngineLevel}   efetivo no Revit: {EffectiveLevel}");
        if (FamilyPath is not null) sb.AppendLine($"Arquivo: {FamilyPath}");
        sb.AppendLine($"Tempo: {Duration.TotalSeconds:0.0} s");
        sb.AppendLine();
        foreach (var stage in Items.Select(i => i.Stage).Distinct())
        {
            var items = Items.Where(i => i.Stage == stage).ToList();
            sb.AppendLine($"{stage}: {items.Count(i => i.Ok)} ok, {items.Count(i => !i.Ok)} com falha");
            foreach (var i in items.Where(i => !i.Ok))
                sb.AppendLine($"  - {i.Subject}: {i.Detail}");
        }
        sb.AppendLine();
        sb.AppendLine($"Envelope: {(EnvelopeOk ? "confere com o modelo" : "DIFERENTE do esperado")} {EnvelopeDetail}");
        sb.AppendLine($"Teste de flexão: {(FlexOk ? "aprovado" : "com falhas")}");
        foreach (var f in FlexResults) sb.AppendLine($"  - {f}");
        if (Warnings.Count > 0)
        {
            sb.AppendLine();
            sb.AppendLine("Avisos do Revit:");
            foreach (var w in Warnings.Distinct().Take(30)) sb.AppendLine($"  - {w}");
        }
        return sb.ToString();
    }

    public void Save(string path)
    {
        File.WriteAllText(path, ToText(), new UTF8Encoding(false));
        var json = JsonSerializer.Serialize(new
        {
            FamilyName, RequestedLevel, EngineLevel, EffectiveLevel, FamilyPath, FlexOk, EnvelopeOk, EnvelopeDetail,
            Items, FlexResults, Warnings, DurationSeconds = Duration.TotalSeconds,
        }, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(Path.ChangeExtension(path, ".json"), json, new UTF8Encoding(false));
    }
}
