using Autodesk.Revit.UI;
using Forge3D.Revit.Commands;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit;

/// <summary>Aplicação do add-in: cria a aba "Forge 3D" na faixa de opções.</summary>
public sealed class App : IExternalApplication
{
    public const string TabName = "Forge 3D";

    public Result OnStartup(UIControlledApplication application)
    {
        try
        {
            try
            {
                application.CreateRibbonTab(TabName);
            }
            catch (Autodesk.Revit.Exceptions.ArgumentException)
            {
                // a aba já existe
            }
            var convert = application.CreateRibbonPanel(TabName, "Converter");
            Add(convert, "Forge3D.Modelo", "Modelo\n3D", typeof(ConvertModelCommand), "3D", Icons.Orange,
                "Converte um modelo 3D em família paramétrica.",
                "SketchUp, 3ds Max, FBX, 3DS, OBJ, STL, GLB/GLTF, DAE, 3MF, STEP, Rhino e SAT. " +
                "Reconhece caixas, cilindros, extrusões e revoluções e cria planos, cotas e parâmetros em português.");
            Add(convert, "Forge3D.Imagem", "Imagem", typeof(ImageCommand), "IMG", Icons.Purple,
                "Gera uma família a partir de uma foto do objeto.",
                "Funciona offline. Revolução e extrusão pela silhueta sem GPU; reconstrução 3D por IA quando o módulo está instalado.");
            var tools = application.CreateRibbonPanel(TabName, "Ferramentas");
            Add(tools, "Forge3D.Biblioteca", "Biblioteca", typeof(LibraryCommand), "BIB", Icons.Blue,
                "Famílias já geradas pelo Forge 3D.", "Busque e insira famílias geradas anteriormente.");
            Add(tools, "Forge3D.Configuracoes", "Configurações", typeof(SettingsCommand), "CFG", Icons.Slate,
                "Padrões, pastas e teste do motor.", "Nível e categoria padrão, pasta das famílias, caminhos do 3ds Max e do SketchUp.");
            Add(tools, "Forge3D.Diagnostico", "Diagnóstico", typeof(DiagnosticsCommand), "DX", Icons.Green,
                "Autoteste do Forge 3D neste Revit.",
                "Constrói famílias de teste, roda o teste de flexão e gera um relatório completo.");
            Log.Info("Forge 3D iniciado.");
            return Result.Succeeded;
        }
        catch (Exception ex)
        {
            Log.Error("Falha ao iniciar o Forge 3D", ex);
            return Result.Failed;
        }
    }

    private static void Add(RibbonPanel panel, string name, string text, Type command, string glyph,
        System.Windows.Media.Color color, string tooltip, string description)
    {
        var data = new PushButtonData(name, text, typeof(App).Assembly.Location, command.FullName);
        if (panel.AddItem(data) is PushButton button)
        {
            button.LargeImage = Icons.Make(glyph, color, 32);
            button.Image = Icons.Make(glyph, color, 16);
            button.ToolTip = tooltip;
            button.LongDescription = description;
        }
    }

    public Result OnShutdown(UIControlledApplication application)
    {
        EngineHost.Shutdown();
        return Result.Succeeded;
    }
}
