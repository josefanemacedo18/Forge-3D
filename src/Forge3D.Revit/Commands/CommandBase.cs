using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Forge3D.Core.Engine;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;
using Forge3D.Revit.UI;

namespace Forge3D.Revit.Commands;

/// <summary>
/// Base dos comandos: qualquer erro vira uma mensagem clara em português e
/// fica no registro; o Revit nunca recebe uma exceção não tratada.
/// </summary>
public abstract class CommandBase : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        try
        {
            return Run(commandData.Application);
        }
        catch (Autodesk.Revit.Exceptions.OperationCanceledException)
        {
            return Result.Cancelled;
        }
        catch (SpecException ex)
        {
            Log.Error("Spec inválido", ex);
            Dialogs.Error("A família não foi criada.", ex.Message, ex.Errors);
            return Result.Cancelled;
        }
        catch (EngineException ex)
        {
            Log.Error("Erro do motor", ex);
            Dialogs.Error("O motor do Forge 3D informou um problema.", ex.Message, ex.Details);
            return Result.Cancelled;
        }
        catch (Exception ex)
        {
            Log.Error($"Erro inesperado em {GetType().Name}", ex);
            Dialogs.Error("Erro inesperado no Forge 3D.", ex.Message + $"\n\nDetalhes no registro: {Log.FilePath}");
            return Result.Cancelled;
        }
    }

    protected abstract Result Run(UIApplication uiapp);

    protected static void SetOwner(System.Windows.Window window, UIApplication uiapp) =>
        _ = new System.Windows.Interop.WindowInteropHelper(window) { Owner = uiapp.MainWindowHandle };
}
