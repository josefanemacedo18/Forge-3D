using System.Windows;
using System.Windows.Threading;

namespace Forge3D.Revit.UI;

/// <summary>
/// Progresso da construção (que roda na thread do Revit). A cada atualização
/// a janela processa a fila de desenho para continuar respondendo.
/// </summary>
public partial class ProgressWindow : Window
{
    public ProgressWindow(string title)
    {
        InitializeComponent();
        TitleText.Text = title;
    }

    public void Report(double fraction, string message)
    {
        Bar.Value = Math.Clamp(fraction * 100, 0, 100);
        MessageText.Text = message;
        Dispatcher.Invoke(DispatcherPriority.Background, new Action(() => { }));
    }
}
