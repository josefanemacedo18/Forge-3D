using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using Forge3D.Revit.Building;

namespace Forge3D.Revit.UI;

public partial class BuildResultWindow : Window
{
    public bool InsertRequested { get; private set; }

    public BuildResultWindow(BuildReport report, bool canInsert)
    {
        InitializeComponent();
        HeaderText.Text = $"Família \"{report.FamilyName}\" criada";
        var level = report.EffectiveLevel;
        LevelText.Text = $"Nível efetivo no Revit: {level}" + (level != report.RequestedLevel ? $" (pedido: {report.RequestedLevel})" : "");
        LevelBadge.Background = new SolidColorBrush(ResultsPanel.LevelColor(level, ""));
        int ok = report.Items.Count(i => i.Ok), fail = report.Items.Count(i => !i.Ok);
        SummaryText.Text = $"{ok} etapas concluídas, {fail} com falha. " +
                           $"Envelope: {(report.EnvelopeOk ? "confere" : "diferente do esperado")}. " +
                           $"Teste de flexão: {(report.FlexOk ? "aprovado" : "com falhas (veja abaixo)")}.";
        PathText.Text = report.FamilyPath ?? "";
        ReportBox.Text = report.ToText();
        InsertButton.IsEnabled = canInsert;
        if (!canInsert) InsertButton.ToolTip = "Abra um projeto (ou família) para inserir.";
        InsertButton.Click += (_, _) => { InsertRequested = true; DialogResult = true; };
        CloseButton.Click += (_, _) => Close();
        FolderButton.Click += (_, _) =>
        {
            if (report.FamilyPath is { } p && File.Exists(p))
                Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{p}\"") { UseShellExecute = true });
        };
    }
}
