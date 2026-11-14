using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Forge3D.Core.Engine;
using Forge3D.Core.Settings;
using Forge3D.Core.Text;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.UI;

public partial class SettingsWindow : Window
{
    private readonly Forge3DSettings _settings;

    public SettingsWindow(Forge3DSettings settings)
    {
        InitializeComponent();
        _settings = settings;
        LevelBox.ItemsSource = Pt.Levels;
        LevelBox.SelectedValue = settings.DefaultLevel;
        CategoryBox.ItemsSource = Pt.Categories;
        CategoryBox.SelectedValue = settings.DefaultCategory;
        AskBBox.IsChecked = settings.AskBeforeLevelB;
        FlexBox.IsChecked = settings.RunFlexTest;
        LoadBox.IsChecked = settings.LoadIntoProject;
        AiMaskBox.IsChecked = settings.UseAiBackgroundRemoval;
        PreloadBox.IsChecked = settings.PreloadEngine;
        FitTolBox.Text = settings.FitTolerance.ToString(CultureInfo.CurrentCulture);
        MaxFacesBox.Text = settings.MaxFreeformFaces.ToString(CultureInfo.CurrentCulture);
        OutputBox.Text = settings.OutputDirectory;
        TemplateBox.Text = settings.FamilyTemplatePath;
        EngineBox.Text = settings.EngineDirectory;
        PythonBox.Text = settings.PythonPath;
        ModelsBox.Text = settings.ModelsDirectory;
        MaxBox.Text = settings.Max3dsBatchPath;
        SketchUpBox.Text = settings.SketchUpPath;
        foreach (var b in PathsGrid.Children.OfType<Button>()) b.Click += PickPath;
        SaveButton.Click += (_, _) => Save();
        CancelButton.Click += (_, _) => Close();
        TestButton.Click += async (_, _) => await TestEngineAsync();
        LogsButton.Click += (_, _) =>
        {
            Directory.CreateDirectory(Forge3DSettings.LogDirectory);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{Forge3DSettings.LogDirectory}\"") { UseShellExecute = true });
        };
    }

    private void PickPath(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string tag }) return;
        var parts = tag.Split('|');
        if (FindName(parts[0]) is not TextBox box) return;
        if (parts[1] == "folder")
        {
            var dlg = new Microsoft.Win32.OpenFolderDialog { Title = "Escolha a pasta" };
            if (dlg.ShowDialog(this) == true) box.Text = dlg.FolderName;
        }
        else
        {
            var dlg = new Microsoft.Win32.OpenFileDialog { Filter = $"{parts[1]}|{parts[2]}|Todos os arquivos (*.*)|*.*" };
            if (dlg.ShowDialog(this) == true) box.Text = dlg.FileName;
        }
    }

    private static double ParsePositive(string text, double fallback) =>
        double.TryParse(text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && v > 0 ? v : fallback;

    private void Apply(Forge3DSettings s)
    {
        s.DefaultLevel = (string)LevelBox.SelectedValue;
        s.DefaultCategory = (string)CategoryBox.SelectedValue;
        s.AskBeforeLevelB = AskBBox.IsChecked == true;
        s.RunFlexTest = FlexBox.IsChecked == true;
        s.LoadIntoProject = LoadBox.IsChecked == true;
        s.UseAiBackgroundRemoval = AiMaskBox.IsChecked == true;
        s.PreloadEngine = PreloadBox.IsChecked == true;
        s.FitTolerance = ParsePositive(FitTolBox.Text, 1.0);
        s.MaxFreeformFaces = (int)ParsePositive(MaxFacesBox.Text, 15000);
        s.OutputDirectory = OutputBox.Text.Trim();
        s.FamilyTemplatePath = TemplateBox.Text.Trim();
        s.EngineDirectory = EngineBox.Text.Trim();
        s.PythonPath = PythonBox.Text.Trim();
        s.ModelsDirectory = ModelsBox.Text.Trim();
        s.Max3dsBatchPath = MaxBox.Text.Trim();
        s.SketchUpPath = SketchUpBox.Text.Trim();
    }

    private void Save()
    {
        Apply(_settings);
        try
        {
            _settings.Save();
            DialogResult = true;
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, $"Não foi possível salvar: {ex.Message}", "Forge 3D", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    private async Task TestEngineAsync()
    {
        var probe = new Forge3DSettings();
        Apply(probe);
        var sb = new StringBuilder();
        var loc = EngineLocator.Resolve(probe, EngineHost.AddinDirectory);
        sb.AppendLine($"Python: {loc.PythonPath} {(File.Exists(loc.PythonPath) ? "(encontrado)" : "(NÃO encontrado)")}");
        sb.AppendLine($"Motor: {loc.EngineDirectory} {(Directory.Exists(Path.Combine(loc.EngineDirectory, "forge3d")) ? "(encontrado)" : "(NÃO encontrado)")}");
        DiagnosticBox.Text = sb + "Iniciando o motor…";
        TestButton.IsEnabled = false;
        try
        {
            using var client = new EngineClient(EngineLocator.StartInfo(probe, EngineHost.AddinDirectory));
            await client.EnsureStartedAsync();
            var ping = await client.PingAsync();
            sb.AppendLine($"Motor {ping.Version} respondeu (Python {ping.Python}, protocolo {ping.Protocol}).");
            var hw = await client.HardwareAsync();
            sb.AppendLine($"Sistema: {Str(hw, "os")} · CPUs: {Str(hw, "cpu_count")} · RAM: {Str(hw, "ram_gb")} GB");
            sb.AppendLine($"PyTorch: {Str(hw, "torch", "não instalado")} · CUDA: {Str(hw, "cuda_available")}");
            if (hw.TryGetProperty("gpus", out var gpus))
                foreach (var g in gpus.EnumerateArray())
                    sb.AppendLine($"GPU: {Str(g, "name")} · {Str(g, "vram_gb")} GB · capacidade {Str(g, "capability", "?")}");
            sb.AppendLine($"Perfil da IA: {Str(hw, "profile")}");
            if (hw.TryGetProperty("ai", out var ai))
                sb.AppendLine($"Módulo de IA 3D: {(ai.TryGetProperty("available", out var av) && av.GetBoolean() ? "pronto" : "indisponível — " + Str(ai, "reason"))}");
        }
        catch (Exception ex)
        {
            sb.AppendLine($"FALHA: {ex.Message}");
            if (ex is EngineException ee) foreach (var d in ee.Details) sb.AppendLine("  " + d);
        }
        finally
        {
            TestButton.IsEnabled = true;
        }
        DiagnosticBox.Text = sb.ToString();
    }

    private static string Str(JsonElement e, string key, string fallback = "")
    {
        if (!e.TryGetProperty(key, out var v) || v.ValueKind == JsonValueKind.Null) return fallback;
        return v.ValueKind == JsonValueKind.String ? v.GetString() ?? fallback : v.ToString();
    }
}
