using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Forge3D.Core.Spec;

namespace Forge3D.Revit.UI;

public sealed record PartRow(string Name, string Kind, string Level);

public sealed record ParamRow(string Name, string Value, string Min);

public partial class ResultsPanel : UserControl
{
    public ResultsPanel() => InitializeComponent();

    public static string KindLabel(string kind) => kind switch
    {
        "box" => "Caixa",
        "cylinder" => "Cilindro",
        "extrusion" => "Extrusão",
        "framed_extrusion" => "Extrusão inclinada",
        "revolution" => "Revolução",
        "mesh" => "Forma livre",
        _ => kind,
    };

    public static Color LevelColor(string level, string kind) => kind == "mesh"
        ? Color.FromRgb(0x86, 0x8E, 0x96)
        : level switch
        {
            "C" => Color.FromRgb(0x2F, 0x9E, 0x44),
            "B" => Color.FromRgb(0xF5, 0x9F, 0x00),
            _ => Color.FromRgb(0xAD, 0xB5, 0xBD),
        };

    public void Show(FamilySpec spec, string? previewPath)
    {
        var level = spec.Family.AchievedLevel;
        LevelText.Text = $"{Forge3D.Core.Text.Pt.LevelLabel(level)} alcançado" +
                         (spec.Family.RequestedLevel != level ? $" (pedido: {spec.Family.RequestedLevel})" : "");
        LevelBadge.Background = new SolidColorBrush(LevelColor(level, ""));
        var e = spec.Envelope;
        EnvelopeText.Text = $"Envelope: {e.Width:0.#} × {e.Depth:0.#} × {e.Height:0.#} mm (L × P × A)";
        CoverageText.Text = $"Volume paramétrico em Nível C: {spec.Report.ParametricCoverage:P0} · " +
                            $"{spec.Solids.Count} sólidos · {spec.Planes.Count} planos · {spec.Parameters.Count} parâmetros";
        SourceText.Text = SourceDescription(spec);

        var parts = spec.Report.Parts.Count > 0
            ? spec.Report.Parts.Select(p => new PartRow(p.Name, KindLabel(p.Kind), p.Level)).ToList()
            : spec.Solids.Select(s => new PartRow(s.Name, KindLabel(s.Kind), "")).ToList();
        PartsList.ItemsSource = parts;

        ParamsList.ItemsSource = spec.Parameters
            .Where(p => p.IsUserFacing || p.Kind == ParameterKinds.Material)
            .Select(p => new ParamRow(p.Name, FormatValue(spec, p), p.Min is { } m ? $"{m:0.#}" : ""))
            .ToList();

        var blockers = spec.Report.LevelCBlockers;
        BlockersTitle.Visibility = BlockersText.Visibility = blockers.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        BlockersText.Text = string.Join(Environment.NewLine, blockers.Select(b => "• " + b));
        var warnings = spec.Report.Warnings.Concat(spec.Report.Flex is { Ok: false } f ? f.Failures.Take(5) : Enumerable.Empty<string>()).ToList();
        WarningsTitle.Visibility = WarningsText.Visibility = warnings.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        WarningsText.Text = string.Join(Environment.NewLine, warnings.Select(w => "• " + w));

        Preview.Clear();
        if (previewPath is not null && File.Exists(previewPath))
        {
            var levels = spec.Report.Parts.ToDictionary(p => p.Id, p => (p.Level, p.Kind));
            try
            {
                Preview.LoadObj(previewPath, id => levels.TryGetValue(id, out var lk) ? LevelColor(lk.Level, lk.Kind) : Colors.Gray);
            }
            catch (Exception ex)
            {
                Forge3D.Revit.Infrastructure.Log.Warn($"Prévia não carregada: {ex.Message}");
            }
        }
    }

    private static string FormatValue(FamilySpec spec, ParameterSpec p)
    {
        if (p.Kind == ParameterKinds.Material)
            return spec.Materials.FirstOrDefault(m => m.Id == p.StringValue)?.Name.Replace("Forge3D ", "") ?? "";
        if (p.Kind == ParameterKinds.Length) return p.NumberValue.ToString("0.#", CultureInfo.CurrentCulture) + " mm";
        return p.Value.ValueKind == JsonValueKind.Number ? p.NumberValue.ToString("0.###", CultureInfo.CurrentCulture) : p.Value.ToString();
    }

    private static string SourceDescription(FamilySpec spec)
    {
        try
        {
            if (spec.Report.Source is { ValueKind: JsonValueKind.Object } src)
            {
                var units = src.TryGetProperty("units", out var u) ? u.GetString() : "?";
                var up = src.TryGetProperty("upAxis", out var a) ? a.GetString() : "?";
                return $"Unidade do arquivo: {units} · eixo vertical: {up}";
            }
        }
        catch (InvalidOperationException)
        {
            // informação opcional
        }
        return "";
    }
}
