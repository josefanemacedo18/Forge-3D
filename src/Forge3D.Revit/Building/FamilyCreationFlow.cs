using System.Windows.Interop;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Forge3D.Core.Library;
using Forge3D.Core.Settings;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;
using Forge3D.Revit.UI;

namespace Forge3D.Revit.Building;

/// <summary>
/// Depois que o usuário aprova a prévia: constrói a família com progresso,
/// guarda na biblioteca, mostra o relatório e insere no projeto se pedido.
/// </summary>
internal static class FamilyCreationFlow
{
    public static Result Run(UIApplication uiapp, Forge3DSettings settings, string templatePath, ConversionOutcome outcome)
    {
        var spec = outcome.Spec;
        var library = new FamilyLibrary(settings.ResolvedOutputDirectory);
        var entryDir = library.NewEntryDirectory(spec.Family.Name);
        var familyPath = Path.Combine(entryDir, FamilyLibrary.SafeFileName(spec.Family.Name) + ".rfa");
        File.Copy(outcome.SpecPath, Path.Combine(entryDir, "familia.familyspec.json"), true);
        if (outcome.PreviewPath is not null && File.Exists(outcome.PreviewPath))
            File.Copy(outcome.PreviewPath, Path.Combine(entryDir, "previa.obj"), true);

        var progress = new ProgressWindow($"Criando \"{spec.Family.Name}\" no Revit…");
        new WindowInteropHelper(progress) { Owner = uiapp.MainWindowHandle };
        BuildResult result;
        try
        {
            progress.Show();
            var builder = new FamilyBuilder(uiapp.Application);
            result = builder.Build(spec, new BuildOptions
            {
                TemplatePath = templatePath,
                OutputPath = familyPath,
                RunFlexTest = settings.RunFlexTest,
                Progress = progress.Report,
            });
        }
        finally
        {
            progress.Close();
        }

        var reportPath = Path.Combine(entryDir, "relatorio.txt");
        try
        {
            result.Report.Save(reportPath);
        }
        catch (Exception ex)
        {
            Log.Warn($"Relatório não salvo: {ex.Message}");
        }
        library.Add(new LibraryEntry
        {
            Id = Guid.NewGuid().ToString("N"),
            Name = spec.Family.Name,
            Category = spec.Family.Category,
            Level = result.Report.EffectiveLevel,
            Source = outcome.SourcePath,
            FamilyPath = familyPath,
            SpecPath = Path.Combine(entryDir, "familia.familyspec.json"),
            ReportPath = reportPath,
            Envelope = new[] { spec.Envelope.Width, spec.Envelope.Depth, spec.Envelope.Height },
        });

        var doc = uiapp.ActiveUIDocument?.Document;
        bool canInsert = doc is not null && !doc.IsReadOnly;
        var window = new BuildResultWindow(result.Report, canInsert && settings.LoadIntoProject);
        new WindowInteropHelper(window) { Owner = uiapp.MainWindowHandle };
        window.ShowDialog();
        if (window.InsertRequested && doc is not null)
        {
            var symbol = FamilyLoader.Load(doc, familyPath);
            FamilyLoader.Place(uiapp.ActiveUIDocument!, symbol);
        }
        return Result.Succeeded;
    }

    /// <summary>Encontra o modelo de família ou pede ao usuário (uma vez).</summary>
    public static string? ResolveTemplate(UIApplication uiapp, Forge3DSettings settings)
    {
        var found = TemplateLocator.Find(settings.FamilyTemplatePath, uiapp.Application.FamilyTemplatePath);
        if (found is not null) return found;
        Dialogs.Info("Modelo de família não encontrado.",
            "Escolha o modelo \"Modelo genérico métrico\" (Metric Generic Model.rft) da sua instalação do Revit. A escolha fica salva.");
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Filter = "Modelo de família (*.rft)|*.rft",
            InitialDirectory = Directory.Exists(uiapp.Application.FamilyTemplatePath) ? uiapp.Application.FamilyTemplatePath : null,
        };
        if (dlg.ShowDialog() != true) return null;
        settings.FamilyTemplatePath = dlg.FileName;
        settings.Save();
        return dlg.FileName;
    }
}
