using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>Carrega a família no documento ativo e inicia a inserção.</summary>
internal static class FamilyLoader
{
    private sealed class OverwriteOptions : IFamilyLoadOptions
    {
        public bool OnFamilyFound(bool familyInUse, out bool overwriteParameterValues)
        {
            overwriteParameterValues = true;
            return true;
        }

        public bool OnSharedFamilyFound(Family sharedFamily, bool familyInUse, out FamilySource source, out bool overwriteParameterValues)
        {
            source = FamilySource.Family;
            overwriteParameterValues = true;
            return true;
        }
    }

    public static FamilySymbol Load(Document doc, string familyPath)
    {
        Family? family = null;
        var name = Path.GetFileNameWithoutExtension(familyPath);
        var r = Tx.Run(doc, "Forge 3D - carregar família", () =>
        {
            if (!doc.LoadFamily(familyPath, new OverwriteOptions(), out family) || family is null)
                family = new FilteredElementCollector(doc).OfClass(typeof(Family)).Cast<Family>().FirstOrDefault(f => f.Name == name);
            if (family is null) throw new InvalidOperationException("o Revit não carregou a família");
            var symbol = family.GetFamilySymbolIds().Select(id => doc.GetElement(id)).OfType<FamilySymbol>().FirstOrDefault()
                         ?? throw new InvalidOperationException("a família não tem tipos");
            if (!symbol.IsActive) symbol.Activate();
        });
        if (!r.Ok) throw new InvalidOperationException("Não foi possível carregar a família: " + r.ErrorText);
        return family!.GetFamilySymbolIds().Select(id => doc.GetElement(id)).OfType<FamilySymbol>().First();
    }

    /// <summary>Inicia a inserção interativa; ESC encerra sem erro.</summary>
    public static void Place(UIDocument uidoc, FamilySymbol symbol)
    {
        try
        {
            uidoc.PromptForFamilyInstancePlacement(symbol);
        }
        catch (Autodesk.Revit.Exceptions.OperationCanceledException)
        {
            // usuário terminou a inserção
        }
        catch (Autodesk.Revit.Exceptions.InvalidOperationException ex)
        {
            Log.Warn($"Inserção não iniciada: {ex.Message}");
            UI.Dialogs.Info("A família foi carregada no projeto.",
                "Para inserir, abra uma vista de planta ou 3D e use o navegador de projeto (Famílias).");
        }
    }
}
