using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using Forge3D.Core.Engine;
using Forge3D.Core.Settings;
using Forge3D.Core.Spec;
using Forge3D.Core.Text;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.UI;

public partial class ImageWindow : Window
{
    private readonly Forge3DSettings _settings;
    private CancellationTokenSource? _cts;
    private FamilySpec? _spec;
    private ConversionResult? _result;
    private string? _workDir;
    private string? _generatedFrom;
    private bool _settingName;

    public ConversionOutcome? Outcome { get; private set; }

    public ImageWindow(Forge3DSettings settings)
    {
        InitializeComponent();
        _settings = settings;
        StrategyBox.ItemsSource = Pt.ImageStrategies;
        StrategyBox.SelectedValue = "auto";
        LevelBox.ItemsSource = Pt.Levels;
        LevelBox.SelectedValue = settings.DefaultLevel;
        CategoryBox.ItemsSource = Pt.Categories;
        CategoryBox.SelectedValue = settings.DefaultCategory;

        BrowseButton.Click += (_, _) => Browse();
        GenerateButton.Click += async (_, _) => await GenerateAsync();
        CancelButton.Click += (_, _) => _cts?.Cancel();
        BuildButton.Click += (_, _) => Approve();
        CloseButton.Click += (_, _) => Close();
        NameBox.TextChanged += (_, _) => { if (!_settingName) NameBox.Tag = null; };
        Drop += (_, e) =>
        {
            if (e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } files) _ = SetFileAsync(files[0]);
        };
        DragOver += (_, e) =>
        {
            e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
            e.Handled = true;
        };
        Closing += (_, e) =>
        {
            if (_cts is null) return;
            _cts.Cancel();
            e.Cancel = true;
            StatusText.Text = "Cancelando… a janela fecha quando o motor parar.";
        };
    }

    private void Browse()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog { Filter = Pt.ImageFileFilter, Title = "Escolha a imagem do objeto" };
        if (dlg.ShowDialog(this) == true) _ = SetFileAsync(dlg.FileName);
    }

    private async Task SetFileAsync(string path)
    {
        FileBox.Text = path;
        if (string.IsNullOrWhiteSpace(NameBox.Text) || NameBox.Tag as string == "auto")
        {
            _settingName = true;
            NameBox.Text = Path.GetFileNameWithoutExtension(path).Replace('_', ' ');
            NameBox.Tag = "auto";
            _settingName = false;
        }
        ShowImage(path);
        await AnalyzeAsync(path);
    }

    private void ShowImage(string path)
    {
        try
        {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.UriSource = new Uri(path);
            bmp.EndInit();
            bmp.Freeze();
            SilhouetteImage.Source = bmp;
        }
        catch (Exception ex)
        {
            AnalysisText.Text = $"Não foi possível exibir a imagem: {ex.Message}";
        }
    }

    private async Task AnalyzeAsync(string path)
    {
        if (!File.Exists(path)) return;
        SetBusy(true);
        _cts = new CancellationTokenSource();
        try
        {
            _workDir = Path.Combine(Path.GetTempPath(), "Forge3D", Guid.NewGuid().ToString("N")[..12]);
            Directory.CreateDirectory(_workDir);
            StatusText.Text = "Iniciando o motor…";
            var client = EngineHost.Get(_settings);
            await client.EnsureStartedAsync(_cts.Token);
            StatusText.Text = "Separando o objeto do fundo…";
            var a = await client.AnalyzeImageAsync(new AnalyzeImageRequest
            {
                Path = path, WorkDirectory = _workDir, UseAiMask = _settings.UseAiBackgroundRemoval,
            }, _cts.Token);
            if (File.Exists(a.Preview)) ShowImage(a.Preview);
            AnalysisText.Text = $"Silhueta por {a.SilhouetteMethod}. Simetria {a.Symmetry:P0}. " +
                                $"Sugestão: {Pt.ImageStrategies.FirstOrDefault(s => s.Value == a.SuggestedStrategy)?.Label ?? a.SuggestedStrategy} — {a.Reason}.";
            bool aiOk = a.Ai.ValueKind == JsonValueKind.Object && a.Ai.TryGetProperty("available", out var av) && av.GetBoolean();
            AiText.Text = aiOk
                ? "Módulo de IA 3D instalado: a estratégia \"Reconstrução 3D por IA\" está disponível."
                : "Módulo de IA 3D não instalado: use Revolução ou Extrusão (o instalador pode adicionar a IA).";
            StatusText.Text = "Imagem analisada. Informe a medida real e clique em Gerar.";
        }
        catch (OperationCanceledException)
        {
            StatusText.Text = "Análise cancelada.";
        }
        catch (EngineException ex)
        {
            StatusText.Text = ex.Message;
            AnalysisText.Text = ex.Message;
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Falha ao analisar imagem", ex);
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
            SetBusy(false);
        }
    }

    private static double? ParseMm(string text) =>
        double.TryParse(text.Replace(',', '.').Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && v > 0 ? v : null;

    private async Task GenerateAsync()
    {
        var path = FileBox.Text.Trim().Trim('"');
        if (!File.Exists(path))
        {
            StatusText.Text = "Imagem não encontrada.";
            return;
        }
        var reference = ParseMm(ReferenceValueBox.Text);
        if (reference is null &&
            MessageBox.Show(this, "Sem uma medida real, a altura será considerada 1000 mm. Continuar mesmo assim?",
                "Forge 3D", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            return;
        SetBusy(true);
        _cts = new CancellationTokenSource();
        var progress = new Progress<EngineProgress>(p =>
        {
            Progress.Value = Math.Clamp(p.Fraction * 100, 0, 100);
            StatusText.Text = p.Message;
        });
        try
        {
            _workDir ??= Path.Combine(Path.GetTempPath(), "Forge3D", Guid.NewGuid().ToString("N")[..12]);
            Directory.CreateDirectory(_workDir);
            var client = EngineHost.Get(_settings);
            await client.EnsureStartedAsync(_cts.Token);
            var refKind = (ReferenceBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "altura";
            _result = await client.ConvertImageAsync(new ConvertImageRequest
            {
                Path = path,
                Output = Path.Combine(_workDir, "familia.familyspec.json"),
                Strategy = (string)StrategyBox.SelectedValue,
                Reference = refKind,
                ReferenceMm = reference,
                DepthMm = ParseMm(DepthBox.Text),
                Level = (string)LevelBox.SelectedValue,
                Category = (string)CategoryBox.SelectedValue,
                FamilyName = string.IsNullOrWhiteSpace(NameBox.Text) ? null : NameBox.Text.Trim(),
                UseAiMask = _settings.UseAiBackgroundRemoval,
                WorkDirectory = _workDir,
            }, progress, _cts.Token);
            _spec = SpecLoader.LoadValidated(_result.SpecPath);
            _generatedFrom = path;
            Results.Show(_spec, _result.PreviewPath);
            Results.Visibility = Visibility.Visible;
            EmptyState.Visibility = Visibility.Collapsed;
            BuildButton.IsEnabled = true;
            var strategy = Pt.ImageStrategies.FirstOrDefault(s => s.Value == _result.Summary.Strategy)?.Label ?? _result.Summary.Strategy;
            StatusText.Text = $"Geometria gerada por {strategy}: {Pt.LevelLabel(_spec.Family.AchievedLevel)}.";
        }
        catch (OperationCanceledException)
        {
            StatusText.Text = "Geração cancelada.";
        }
        catch (EngineException ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Erro do motor na imagem", ex);
            Dialogs.Error("O motor não conseguiu gerar a família a partir da imagem.", ex.Message, ex.Details);
        }
        catch (SpecException ex)
        {
            StatusText.Text = ex.Message;
            Dialogs.Error("O resultado é inválido.", ex.Message, ex.Errors);
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Falha na geração por imagem", ex);
            Dialogs.Error("Não foi possível gerar a família.", ex.Message);
        }
        finally
        {
            _cts?.Dispose();
            _cts = null;
            SetBusy(false);
        }
    }

    private void SetBusy(bool busy)
    {
        GenerateButton.IsEnabled = !busy;
        BrowseButton.IsEnabled = !busy;
        BuildButton.IsEnabled = !busy && _spec is not null;
        CancelButton.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = busy ? Visibility.Visible : Visibility.Hidden;
        if (busy) Progress.Value = 0;
    }

    private void Approve()
    {
        if (_spec is null || _result is null || _generatedFrom is null) return;
        if (_spec.Family.RequestedLevel == "C" && _spec.Family.AchievedLevel != "C" && _settings.AskBeforeLevelB)
        {
            if (!Dialogs.AskSwitchToLevelB(_spec)) return;
        }
        Outcome = new ConversionOutcome(_spec, _result.SpecPath, _result.PreviewPath, _generatedFrom, "imagem");
        DialogResult = true;
    }
}
