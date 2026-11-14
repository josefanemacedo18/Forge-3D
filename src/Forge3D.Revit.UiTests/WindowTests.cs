using System.IO;
using System.Runtime.ExceptionServices;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Threading;
using Forge3D.Core.Library;
using Forge3D.Core.Settings;
using Forge3D.Core.Spec;
using Forge3D.Revit.Building;
using Forge3D.Revit.UI;
using Xunit;

namespace Forge3D.Revit.UiTests;

/// <summary>
/// Abre cada janela do plugin de verdade (fora da tela) e fecha. Pega o tipo
/// de erro que o compilador não vê: um Setter inválido no tema derrubava
/// todas as janelas com "System.Windows.Setter.Property iniciou uma exceção".
/// </summary>
public class WindowTests
{
    private static string Dir => AppContext.BaseDirectory;

    static WindowTests()
    {
        _ = System.IO.Packaging.PackUriHelper.UriSchemePack; // registra o esquema pack://
        _ = typeof(ResultsPanel).Assembly; // garante o assembly do plugin carregado (recursos /Forge3D.Revit;component)
    }

    /// <summary>Roda numa thread STA própria (exigência do WPF) e repassa a exceção.</summary>
    private static void Sta(Action action)
    {
        Exception? error = null;
        var thread = new Thread(() =>
        {
            try
            {
                action();
            }
            catch (Exception ex)
            {
                error = ex;
            }
            finally
            {
                Dispatcher.CurrentDispatcher.InvokeShutdown();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        Assert.True(thread.Join(TimeSpan.FromMinutes(2)), "a janela travou");
        if (error is not null) ExceptionDispatchInfo.Capture(error).Throw();
    }

    /// <summary>Mostra fora da tela, processa a fila (layout, Loaded) e fecha.</summary>
    private static void ShowAndClose(Window w)
    {
        w.ShowActivated = false;
        w.WindowStartupLocation = WindowStartupLocation.Manual;
        w.Left = -32000;
        w.Top = -32000;
        w.Show();
        w.UpdateLayout();
        Dispatcher.CurrentDispatcher.Invoke(() => { }, DispatcherPriority.Background);
        Assert.True(w.ActualWidth > 0, "a janela não foi desenhada");
        w.Close();
    }

    [Fact]
    public void Theme_loads_and_every_style_applies()
    {
        Sta(() =>
        {
            var dict = (ResourceDictionary)Application.LoadComponent(
                new Uri("/Forge3D.Revit;component/UI/Theme.xaml", UriKind.Relative));
            var styles = dict.Values.OfType<Style>().ToList();
            Assert.NotEmpty(styles);
            foreach (var style in styles)
            {
                var target = (FrameworkElement)Activator.CreateInstance(style.TargetType)!;
                target.Style = style; // aplica todos os Setters
                if (target is Window w) ShowAndClose(w);
            }
            Assert.IsType<Style>(dict["Forge3DWindow"]);
        });
    }

    public static IEnumerable<object[]> XamlFiles() =>
        Directory.GetFiles(Path.Combine(AppContext.BaseDirectory, "xaml"), "*.xaml")
            .Where(f => !f.EndsWith("Theme.xaml", StringComparison.OrdinalIgnoreCase))
            .Select(f => new object[] { Path.GetFileName(f) });

    [Theory]
    [MemberData(nameof(XamlFiles))]
    public void Every_window_and_panel_loads_from_its_xaml(string file)
    {
        // O XAML da própria janela, sem a classe de código: estilos, recursos e
        // controles são resolvidos exatamente como no Revit.
        var xaml = File.ReadAllText(Path.Combine(Dir, "xaml", file));
        xaml = Regex.Replace(xaml, @"\s+x:Class=""[^""]*""", "");
        xaml = xaml.Replace("xmlns:local=\"clr-namespace:Forge3D.Revit.UI\"",
                            "xmlns:local=\"clr-namespace:Forge3D.Revit.UI;assembly=Forge3D.Revit\"");
        Sta(() =>
        {
            var root = XamlReader.Parse(xaml);
            if (root is Window w) ShowAndClose(w);
            else ShowAndClose(new Window { Width = 900, Height = 600, Content = (UIElement)root });
        });
    }

    [Fact]
    public void Real_windows_open_with_their_code()
    {
        var tmp = Directory.CreateTempSubdirectory("forge3d-ui").FullName;
        var specs = Directory.GetFiles(Path.Combine(Dir, "Fixtures"), "*.json").Select(SpecLoader.Load).ToList();
        Sta(() =>
        {
            var settings = new Forge3DSettings();
            ShowAndClose(new ProgressWindow("Teste"));
            ShowAndClose(new SettingsWindow(settings));
            ShowAndClose(new ImageWindow(settings));
            ShowAndClose(new LibraryWindow(new FamilyLibrary(tmp), canInsert: false));
            var report = new BuildReport(specs[0]);
            report.Ok("Teste", "item");
            report.Fail("Teste", "outro", "motivo");
            ShowAndClose(new BuildResultWindow(report, canInsert: true));
            // O painel de resultados com cada família de referência.
            foreach (var spec in specs)
            {
                var panel = new ResultsPanel();
                var host = new Window { Width = 1000, Height = 700, Content = panel };
                panel.Show(spec, null);
                ShowAndClose(host);
            }
        });
    }

    [Fact]
    public void Convert_window_opens_with_its_code()
    {
        Sta(() =>
        {
            var w = new ConvertWindow((_, _, _) => throw new NotSupportedException("sem Revit no teste"), new Forge3DSettings());
            ShowAndClose(w);
        });
    }

    [Fact]
    public void Invalid_style_setter_is_caught_by_these_tests()
    {
        // Sentinela: o erro que o usuário viu ("Setter.Property iniciou uma
        // exceção") precisa ser detectado neste ambiente de teste.
        const string bad = "<ResourceDictionary xmlns='http://schemas.microsoft.com/winfx/2006/xaml/presentation'>" +
                           "<Style TargetType='Window'><Setter Property='WindowStartupLocation' Value='CenterOwner'/></Style>" +
                           "</ResourceDictionary>";
        Sta(() => Assert.ThrowsAny<Exception>(() => XamlReader.Parse(bad)));
    }

    [Fact]
    public void Ribbon_icons_render()
    {
        Sta(() =>
        {
            var icon = Forge3D.Revit.Infrastructure.Icons.Make("3D", Forge3D.Revit.Infrastructure.Icons.Orange, 32);
            Assert.Equal(32, (int)icon.Width);
        });
    }
}
