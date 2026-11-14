using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.UI;
using Forge3D.Core.Engine;
using Forge3D.Core.Settings;
using Forge3D.Core.Spec;
using Forge3D.Revit.Building;
using Forge3D.Revit.Infrastructure;
using Forge3D.Revit.UI;

namespace Forge3D.Revit.Commands;

/// <summary>
/// Autoteste no Revit: verifica o motor e constrói famílias de teste que
/// exercitam todas as técnicas (planos, cotas rotuladas, fórmulas, faces
/// travadas, diâmetros, centros, vazios, revoluções, formas livres e
/// flexão). Gera um relatório para enviar caso algo falhe.
/// </summary>
[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class DiagnosticsCommand : CommandBase
{
    protected override Result Run(UIApplication uiapp)
    {
        var settings = Forge3DSettings.Load();
        var template = FamilyCreationFlow.ResolveTemplate(uiapp, settings);
        if (template is null) return Result.Cancelled;
        var sb = new StringBuilder();
        var app = uiapp.Application;
        sb.AppendLine("Forge 3D — Diagnóstico");
        sb.AppendLine($"Data: {DateTime.Now:yyyy-MM-dd HH:mm}");
        sb.AppendLine($"Revit: {app.VersionName} ({app.VersionBuild}) · idioma {app.Language}");
        sb.AppendLine($"Plugin: {typeof(App).Assembly.GetName().Version}");
        sb.AppendLine($"Modelo de família: {template}");
        sb.AppendLine();

        // 1) Motor.
        sb.AppendLine("== Motor ==");
        try
        {
            var client = EngineHost.Get(settings);
            Task.Run(() => client.EnsureStartedAsync()).GetAwaiter().GetResult();
            var ping = Task.Run(() => client.PingAsync()).GetAwaiter().GetResult();
            sb.AppendLine($"Motor {ping.Version} ok (Python {ping.Python}).");
            var hw = Task.Run(() => client.HardwareAsync()).GetAwaiter().GetResult();
            sb.AppendLine(JsonSerializer.Serialize(hw, new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception ex)
        {
            sb.AppendLine($"FALHA: {ex.Message}");
            if (ex is EngineException ee) foreach (var d in ee.Details) sb.AppendLine("  " + d);
        }
        sb.AppendLine();

        // 2) Famílias de teste.
        var testsDir = Path.Combine(EngineHost.AddinDirectory, "diagnostico");
        var outDir = Path.Combine(Path.GetTempPath(), "Forge3D", "diagnostico");
        Directory.CreateDirectory(outDir);
        var files = Directory.Exists(testsDir) ? Directory.GetFiles(testsDir, "*.json").OrderBy(f => f).ToArray() : Array.Empty<string>();
        var totals = new Dictionary<string, (int Ok, int Fail)>();
        int flexFails = 0, envelopeFails = 0, crashed = 0;
        var progress = new ProgressWindow("Diagnóstico: construindo famílias de teste…");
        SetOwner(progress, uiapp);
        progress.Show();
        try
        {
            for (int i = 0; i < files.Length; i++)
            {
                var name = Path.GetFileNameWithoutExtension(files[i]);
                sb.AppendLine($"== Teste: {name} ==");
                try
                {
                    var spec = SpecLoader.LoadValidated(files[i]);
                    var result = new FamilyBuilder(app).Build(spec, new BuildOptions
                    {
                        TemplatePath = template,
                        OutputPath = Path.Combine(outDir, name + ".rfa"),
                        RunFlexTest = true,
                        Progress = (f, m) => progress.Report((i + f) / files.Length, $"{name}: {m}"),
                    });
                    sb.AppendLine(result.Report.ToText());
                    foreach (var item in result.Report.Items)
                    {
                        totals.TryGetValue(item.Stage, out var t);
                        totals[item.Stage] = item.Ok ? (t.Ok + 1, t.Fail) : (t.Ok, t.Fail + 1);
                    }
                    if (!result.Report.FlexOk) flexFails++;
                    if (!result.Report.EnvelopeOk) envelopeFails++;
                }
                catch (Exception ex)
                {
                    crashed++;
                    sb.AppendLine($"FALHA GERAL: {ex}");
                }
                sb.AppendLine();
            }
        }
        finally
        {
            progress.Close();
        }

        var summary = new StringBuilder();
        summary.AppendLine($"Famílias de teste: {files.Length} (falhas gerais: {crashed})");
        foreach (var (stage, (ok, fail)) in totals.OrderBy(k => k.Key))
            summary.AppendLine($"{stage}: {ok} ok, {fail} com falha");
        summary.AppendLine($"Envelope diferente do esperado: {envelopeFails}");
        summary.AppendLine($"Testes de flexão com falha: {flexFails}");
        sb.Insert(0, summary + Environment.NewLine);

        var docs = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Forge3D");
        Directory.CreateDirectory(docs);
        var reportPath = Path.Combine(docs, $"Diagnostico_{DateTime.Now:yyyyMMdd_HHmm}.txt");
        File.WriteAllText(reportPath, sb.ToString(), new UTF8Encoding(false));
        Log.Info($"Diagnóstico salvo em {reportPath}");

        var td = new TaskDialog(Dialogs.Title)
        {
            MainInstruction = crashed == 0 && totals.Values.All(v => v.Fail == 0) && flexFails == 0
                ? "Diagnóstico concluído: tudo funcionando."
                : "Diagnóstico concluído com pontos de atenção.",
            MainContent = summary + $"\nRelatório completo: {reportPath}",
            CommonButtons = TaskDialogCommonButtons.Close,
        };
        td.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Abrir o relatório");
        if (td.Show() == TaskDialogResult.CommandLink1)
            Process.Start(new ProcessStartInfo(reportPath) { UseShellExecute = true });
        return Result.Cancelled;
    }
}
