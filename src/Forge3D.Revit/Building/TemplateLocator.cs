using System.Globalization;
using System.Text;

namespace Forge3D.Revit.Building;

/// <summary>
/// Localiza o modelo de família "Modelo genérico" em qualquer idioma
/// instalado do Revit. O Forge 3D cria a família a partir dele e troca a
/// categoria depois (funciona para todas as categorias suportadas).
/// </summary>
internal static class TemplateLocator
{
    private static readonly string[] Wanted =
    {
        "generic model", "modelo generico", "modele generique", "allgemeines modell", "modello generico",
        "model ogolny", "obecny model", "general model", "genericky model",
    };

    private static readonly string[] Rejected =
    {
        "face", "wall", "ceiling", "floor", "roof", "line", "adaptive", "pattern", "two level", "based", "hosted",
        "baseado", "baseada", "parede", "teto", "forro", "piso", "telhado", "linha", "adaptativo", "padrao", "dois niveis",
        "mass", "massa", "profile", "perfil", "detail", "detalhe", "annotation", "anotacao", "rpc", "voisin",
        "mur", "plafond", "sol", "toit", "ligne", "adaptatif", "wand", "decke", "boden", "dach", "linie",
    };

    public static string? Find(string? configured, string? revitTemplateFolder)
    {
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(configured)) return configured;
        var folders = new List<string>();
        if (!string.IsNullOrWhiteSpace(revitTemplateFolder) && Directory.Exists(revitTemplateFolder))
        {
            folders.Add(revitTemplateFolder);
            var parent = Directory.GetParent(revitTemplateFolder)?.FullName;
            if (parent is not null) folders.Add(parent);
        }
        var programData = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
        folders.Add(Path.Combine(programData, "Autodesk", "RVT 2027", "Family Templates"));
        string? best = null;
        int bestScore = 0;
        foreach (var folder in folders.Distinct())
        {
            if (!Directory.Exists(folder)) continue;
            IEnumerable<string> files;
            try
            {
                files = Directory.EnumerateFiles(folder, "*.rft", SearchOption.AllDirectories);
            }
            catch (Exception)
            {
                continue;
            }
            foreach (var f in files)
            {
                int score = Score(Path.GetFileNameWithoutExtension(f));
                // Prefere o idioma configurado no Revit (pasta indicada primeiro).
                if (revitTemplateFolder is not null && f.StartsWith(revitTemplateFolder, StringComparison.OrdinalIgnoreCase)) score += 2;
                if (score > bestScore)
                {
                    bestScore = score;
                    best = f;
                }
            }
            if (best is not null && folder == revitTemplateFolder) break;
        }
        return best;
    }

    internal static int Score(string name)
    {
        var n = Normalize(name);
        if (!Wanted.Any(n.Contains)) return 0;
        if (Rejected.Any(r => n.Contains(r))) return 0;
        int score = 10;
        if (n.Contains("metric") || n.Contains("metrico") || n.Contains("metrique") || n.Contains("metrisch")) score += 3;
        return score;
    }

    private static string Normalize(string s)
    {
        var formD = s.ToLowerInvariant().Normalize(NormalizationForm.FormD);
        var sb = new StringBuilder();
        foreach (var c in formD)
            if (CharUnicodeInfo.GetUnicodeCategory(c) != UnicodeCategory.NonSpacingMark) sb.Append(c);
        return sb.ToString().Replace('-', ' ').Replace('_', ' ');
    }
}
