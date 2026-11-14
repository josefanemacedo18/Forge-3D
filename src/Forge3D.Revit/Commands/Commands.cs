using Autodesk.Revit.Attributes;
using Autodesk.Revit.UI;
using Forge3D.Core.Library;
using Forge3D.Core.Settings;
using Forge3D.Revit.Building;
using Forge3D.Revit.Infrastructure;
using Forge3D.Revit.UI;

namespace Forge3D.Revit.Commands;

/// <summary>Converter modelo 3D (SketchUp, 3ds Max, FBX, OBJ...) em família.</summary>
[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class ConvertModelCommand : CommandBase
{
    protected override Result Run(UIApplication uiapp)
    {
        var settings = Forge3DSettings.Load();
        var template = FamilyCreationFlow.ResolveTemplate(uiapp, settings);
        if (template is null) return Result.Cancelled;
        var window = new ConvertWindow(uiapp, settings, template);
        SetOwner(window, uiapp);
        if (window.ShowDialog() != true || window.Outcome is null) return Result.Cancelled;
        return FamilyCreationFlow.Run(uiapp, settings, template, window.Outcome);
    }
}

/// <summary>Gerar família a partir de uma imagem.</summary>
[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class ImageCommand : CommandBase
{
    protected override Result Run(UIApplication uiapp)
    {
        var settings = Forge3DSettings.Load();
        var template = FamilyCreationFlow.ResolveTemplate(uiapp, settings);
        if (template is null) return Result.Cancelled;
        var window = new ImageWindow(settings);
        SetOwner(window, uiapp);
        if (window.ShowDialog() != true || window.Outcome is null) return Result.Cancelled;
        return FamilyCreationFlow.Run(uiapp, settings, template, window.Outcome);
    }
}

/// <summary>Biblioteca das famílias geradas.</summary>
[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class LibraryCommand : CommandBase
{
    protected override Result Run(UIApplication uiapp)
    {
        var settings = Forge3DSettings.Load();
        var doc = uiapp.ActiveUIDocument?.Document;
        var window = new LibraryWindow(new FamilyLibrary(settings.ResolvedOutputDirectory), doc is { IsReadOnly: false });
        SetOwner(window, uiapp);
        if (window.ShowDialog() != true || window.Selected is null || doc is null) return Result.Cancelled;
        var symbol = FamilyLoader.Load(doc, window.Selected.FamilyPath);
        FamilyLoader.Place(uiapp.ActiveUIDocument!, symbol);
        return Result.Succeeded;
    }
}

/// <summary>Configurações do Forge 3D.</summary>
[Transaction(TransactionMode.Manual)]
[Regeneration(RegenerationOption.Manual)]
public sealed class SettingsCommand : CommandBase
{
    protected override Result Run(UIApplication uiapp)
    {
        var settings = Forge3DSettings.Load();
        var window = new SettingsWindow(settings);
        SetOwner(window, uiapp);
        if (window.ShowDialog() == true) EngineHost.Shutdown(); // reinicia com os novos caminhos
        return Result.Cancelled; // nada mudou no modelo
    }
}
