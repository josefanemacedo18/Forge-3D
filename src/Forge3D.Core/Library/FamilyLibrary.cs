using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Forge3D.Core.Library;

/// <summary>Registro de uma família gerada pelo Forge 3D.</summary>
public sealed class LibraryEntry
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("level")] public string Level { get; set; } = "";
    [JsonPropertyName("source")] public string Source { get; set; } = "";
    [JsonPropertyName("sourceSha256")] public string? SourceSha256 { get; set; }
    [JsonPropertyName("familyPath")] public string FamilyPath { get; set; } = "";
    [JsonPropertyName("specPath")] public string SpecPath { get; set; } = "";
    [JsonPropertyName("reportPath")] public string? ReportPath { get; set; }
    [JsonPropertyName("created")] public DateTime Created { get; set; } = DateTime.Now;
    [JsonPropertyName("envelope")] public double[] Envelope { get; set; } = new double[3];
    [JsonPropertyName("tags")] public List<string> Tags { get; set; } = new();
}

/// <summary>
/// Biblioteca local: cada família fica numa pasta própria (família .rfa,
/// spec, relatório e prévia) e o índice em "biblioteca.json".
/// </summary>
public sealed class FamilyLibrary
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };
    private readonly object _lock = new();

    public string Root { get; }
    public string IndexPath => Path.Combine(Root, "biblioteca.json");

    public FamilyLibrary(string root)
    {
        Root = root;
        Directory.CreateDirectory(Root);
    }

    public List<LibraryEntry> Load()
    {
        lock (_lock)
        {
            try
            {
                if (!File.Exists(IndexPath)) return new List<LibraryEntry>();
                return JsonSerializer.Deserialize<List<LibraryEntry>>(File.ReadAllText(IndexPath), Options) ?? new();
            }
            catch (JsonException)
            {
                return new List<LibraryEntry>();
            }
        }
    }

    /// <summary>Pasta nova e exclusiva para uma família ("Nome", "Nome (2)"...).</summary>
    public string NewEntryDirectory(string familyName)
    {
        var safe = SafeFileName(familyName);
        var dir = Path.Combine(Root, safe);
        int k = 2;
        while (Directory.Exists(dir)) dir = Path.Combine(Root, $"{safe} ({k++})");
        Directory.CreateDirectory(dir);
        return dir;
    }

    public void Add(LibraryEntry entry)
    {
        lock (_lock)
        {
            var all = Load();
            all.RemoveAll(e => e.Id == entry.Id);
            all.Insert(0, entry);
            var tmp = IndexPath + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(all, Options), new UTF8Encoding(false));
            File.Move(tmp, IndexPath, overwrite: true);
        }
    }

    public LibraryEntry? FindBySource(string sha256) =>
        Load().FirstOrDefault(e => e.SourceSha256 == sha256 && File.Exists(e.FamilyPath));

    public static string SafeFileName(string name)
    {
        var invalid = Path.GetInvalidFileNameChars().Concat(new[] { '<', '>', ':', '"', '/', '\\', '|', '?', '*' }).ToHashSet();
        var chars = name.Trim().Select(c => invalid.Contains(c) ? '_' : c).ToArray();
        var s = new string(chars).Trim('.', ' ');
        if (string.IsNullOrEmpty(s)) s = "Familia";
        return s.Length > 80 ? s[..80] : s;
    }
}
