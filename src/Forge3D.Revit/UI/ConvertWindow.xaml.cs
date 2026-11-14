using System.Globalization;
using System.Windows;
using Autodesk.Revit.UI;
using Forge3D.Core.Engine;
using Forge3D.Core.Settings;
using Forge3D.Core.Spec;
using Forge3D.Core.Text;
using Forge3D.Revit.Import;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.UI;

public partial class ConvertWindow : Window
{
    private readonly UIApplication _uiapp;
    private readonly Forge3DSettings _settings;
    private readonly string _templatePath;
    private CancellationTokenSource? _cts;
    private FamilySpec? _spec;
    private ConversionResult? _result;
    private PreparedModel? _prepared;
    private string? _analyzedPath;
    private bool _settingName;

    public ConversionOutcome? Outcome { get; private set; }

    public ConvertWindow(UIApplication uiapp, Forge3DSettings settings, string templatePath)
    {
        InitializeComponent();
        _uiapp = uiapp;
        _settings = settings;
        _templatePath = templatePath;
        LevelBox.ItemsSource = Pt.Levels;
        LevelBox.SelectedValue = settings.DefaultLevel;
        CategoryBox.ItemsSource = Pt.Categories;
        CategoryBox.SelectedValue = settings.DefaultCategory;
        UnitsBox.ItemsSource = Pt.Units;
        UnitsBox.SelectedValue = "auto";
        UpBox.ItemsSource = Pt.UpAxes;
        UpBox.SelectedValue = "auto";
        FitTolBox.Text = settings.FitTolerance.ToString(CultureInfo.CurrentCulture);
        MaxFacesBox.Text = settings.MaxFreeformFaces.ToString(CultureInfo.CurrentCulture);

        BrowseButton.Click += (_, _) => Browse();
        AnalyzeButton.Click += async (_, _) => await AnalyzeAsync();
        CancelButton.Click += (_, _) => _cts?.Cancel();
        BuildButton.Click += (_, _) => Approve();
        CloseButton.Click += (_, _) => Close();
        FileBox.TextChanged += (_, _) => InvalidateAnalysis();
        NameBox.TextChanged += (_, _) => { if (!_settingName) NameBox.Tag = null; };
        foreach (var box in new[] { LevelBox, CategoryBox, UnitsBox, UpBox })
            box.SelectionChanged += (_, _) => InvalidateAnalysis();
        Drop += OnDrop;
        DragOver += (_, e) =>
        {
            e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
            e.Handled = true;
        };
        Closing += (_, e) =>
        {
            if (_cts is not null)
            {
                _cts.Cancel();
                e.Cancel = true;
                StatusText.Text = "Cancelando… a janela fecha quando o motor parar.";
            }
        };
    }

    private void Browse()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog { Filter = Pt.ModelFileFilter, Title = "Escolha o modelo 3D" };
        if (dlg.ShowDialog(this) == true) SetFile(dlg.FileName);
    }

    private void OnDrop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } files) SetFile(files[0]);
    }

    private void SetFile(string path)
    {
        FileBox.Text = path;
        if (string.IsNullOrWhiteSpace(NameBox.Text) || NameBox.Tag as string == "auto")
        {
            _settingName = true;
            NameBox.Text = Path.GetFileNameWithoutExtension(path).Replace('_', ' ');
            NameBox.Tag = "auto";
            _settingName = false;
        }
    }

    private void InvalidateAnalysis()
    {
        if (_spec is null) return;
        _spec = null;
        _result = null;
        BuildButton.IsEnabled = false;
        StatusText.Text = "Opções alteradas: analise novamente.";
    }

    private double ParseDouble(string text, double fallback) =>
        double.TryParse(text.Replace(',', '.'), NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && v > 0 ? v : fallback;

    private async Task AnalyzeAsync()
    {
        var path = FileBox.Text.Trim().Trim('"');
        if (!File.Exists(path))
        {
            StatusText.Text = "Arquivo não encontrado.";
            return;
        }
        if (!ModelImporter.IsSupported(path))
        {
            StatusText.Text = $"Formato '{Path.GetExtension(path)}' não suportado.";
            return;
        }
        SetBusy(true);
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;
        var progress = new Progress<EngineProgress>(p =>
        {
            Progress.Value = Math.Clamp(p.Fraction * 100, 0, 100);
            StatusText.Text = p.Message;
        });
        try
        {
            var workDir = Path.Combine(Path.GetTempPath(), "Forge3D", Guid.NewGuid().ToString("N")[..12]);
            Directory.CreateDirectory(workDir);
            StatusText.Text = "Preparando o arquivo…";
            var ext = Path.GetExtension(path);
            if (ModelImporter.Via3dsMax.Contains(ext))
            {
                StatusText.Text = "Exportando pelo 3ds Max (pode levar alguns minutos)…";
                _prepared = await Task.Run(() => ModelImporter.Prepare(_uiapp.Application, _uiapp.ActiveUIDocument?.Document,
                    path, _settings, _templatePath, workDir, ct), ct);
            }
            else
            {
                // Rotas pelo Revit usam a API: rodam na thread da interface.
                _prepared = ModelImporter.Prepare(_uiapp.Application, _uiapp.ActiveUIDocument?.Document, path, _settings,
                    _templatePath, workDir, ct);
            }
            var client = EngineHost.Get(_settings);
            StatusText.Text = "Iniciando o motor…";
            await client.EnsureStartedAsync(ct);
            var request = new ConvertMeshRequest
            {
                Path = _prepared.MeshPath,
                Output = Path.Combine(workDir, "familia.familyspec.json"),
                Level = (string)LevelBox.SelectedValue,
                Units = _prepared.Route == "motor" ? (string)UnitsBox.SelectedValue : "mm",
                UpAxis = _prepared.Route == "motor" ? (string)UpBox.SelectedValue : "Z",
                Category = (string)CategoryBox.SelectedValue,
                FamilyName = string.IsNullOrWhiteSpace(NameBox.Text) ? null : NameBox.Text.Trim(),
                FitTolerance = ParseDouble(FitTolBox.Text, 1.0),
                MaxFreeformFaces = (int)ParseDouble(MaxFacesBox.Text, 15000),
                SheetThickness = ParseDouble(SheetBox.Text, 3.0),
            };
            _result = await client.ConvertMeshAsync(request, progress, ct);
            _spec = SpecLoader.LoadValidated(_result.SpecPath);
            _analyzedPath = path;
            Results.Show(_spec, _result.PreviewPath);
            Results.Visibility = Visibility.Visible;
            EmptyState.Visibility = Visibility.Collapsed;
            BuildButton.IsEnabled = true;
            var notes = string.Join(" ", _prepared.Notes);
            StatusText.Text = $"Análise concluída: {Pt.LevelLabel(_spec.Family.AchievedLevel)}. {notes}".Trim();
        }
        catch (OperationCanceledException)
        {
            StatusText.Text = "Análise cancelada.";
        }
        catch (EngineException ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Erro do motor na análise", ex);
            Dialogs.Error("O motor não conseguiu converter o arquivo.", ex.Message, ex.Details);
        }
        catch (SpecException ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Spec inválido", ex);
            Dialogs.Error("O resultado da análise é inválido.", ex.Message, ex.Errors);
        }
        catch (Exception ex)
        {
            StatusText.Text = ex.Message;
            Log.Error("Falha na análise", ex);
            Dialogs.Error("Não foi possível analisar o arquivo.", ex.Message);
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
        AnalyzeButton.IsEnabled = !busy;
        BrowseButton.IsEnabled = !busy;
        BuildButton.IsEnabled = !busy && _spec is not null;
        CancelButton.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = busy ? Visibility.Visible : Visibility.Hidden;
        if (busy) Progress.Value = 0;
    }

    private void Approve()
    {
        if (_spec is null || _result is null || _prepared is null || _analyzedPath is null) return;
        if (_spec.Family.RequestedLevel == "C" && _spec.Family.AchievedLevel != "C" && _settings.AskBeforeLevelB)
        {
            if (!Dialogs.AskSwitchToLevelB(_spec)) return;
        }
        Outcome = new ConversionOutcome(_spec, _result.SpecPath, _result.PreviewPath, _analyzedPath, _prepared.Route);
        DialogResult = true;
    }
}
