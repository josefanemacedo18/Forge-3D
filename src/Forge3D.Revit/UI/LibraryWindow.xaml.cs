using System.Diagnostics;
using System.Globalization;
using System.Windows;
using Forge3D.Core.Library;
using Forge3D.Core.Text;

namespace Forge3D.Revit.UI;

public sealed record LibraryRow(string Name, string Category, string Level, string Source, string Created, LibraryEntry Entry);

public partial class LibraryWindow : Window
{
    private readonly List<LibraryRow> _all;

    public LibraryEntry? Selected { get; private set; }

    public LibraryWindow(FamilyLibrary library, bool canInsert)
    {
        InitializeComponent();
        _all = library.Load()
            .Where(e => File.Exists(e.FamilyPath))
            .Select(e => new LibraryRow(e.Name, Pt.CategoryLabel(e.Category), e.Level, Path.GetFileName(e.Source),
                e.Created.ToString("g", CultureInfo.CurrentCulture), e))
            .ToList();
        List.ItemsSource = _all;
        InsertButton.IsEnabled = canInsert;
        SearchBox.TextChanged += (_, _) =>
        {
            var q = SearchBox.Text.Trim();
            List.ItemsSource = string.IsNullOrEmpty(q)
                ? _all
                : _all.Where(r => (r.Name + " " + r.Category + " " + r.Source).Contains(q, StringComparison.CurrentCultureIgnoreCase)).ToList();
        };
        InsertButton.Click += (_, _) =>
        {
            if (List.SelectedItem is LibraryRow row)
            {
                Selected = row.Entry;
                DialogResult = true;
            }
        };
        List.MouseDoubleClick += (_, _) =>
        {
            if (canInsert && List.SelectedItem is LibraryRow row)
            {
                Selected = row.Entry;
                DialogResult = true;
            }
        };
        FolderButton.Click += (_, _) =>
        {
            var target = (List.SelectedItem as LibraryRow)?.Entry.FamilyPath;
            var args = target is not null ? $"/select,\"{target}\"" : $"\"{library.Root}\"";
            Process.Start(new ProcessStartInfo("explorer.exe", args) { UseShellExecute = true });
        };
        CloseButton.Click += (_, _) => Close();
    }
}
