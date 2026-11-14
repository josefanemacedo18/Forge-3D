using System.Globalization;
using System.Text;

namespace Forge3D.Core.Mesh;

/// <summary>Malha simples (milímetros, Z para cima) com nome de parte e cor.</summary>
public sealed class MeshPart
{
    public string Name { get; init; } = "Parte";
    public List<(double X, double Y, double Z)> Vertices { get; } = new();
    public List<(int A, int B, int C)> Triangles { get; } = new();
    public (byte R, byte G, byte B)? Color { get; init; }
    public string? Material { get; init; }
}

/// <summary>
/// Grava OBJ com o cabeçalho "# forge3d units=mm up=Z", que o motor reconhece
/// (dispensa adivinhar unidade e eixo). Cores vão num MTL ao lado.
/// </summary>
public static class ObjWriter
{
    public const string Header = "# forge3d units=mm up=Z";

    public static void Write(string path, IReadOnlyList<MeshPart> parts)
    {
        var inv = CultureInfo.InvariantCulture;
        var mtlName = Path.GetFileNameWithoutExtension(path) + ".mtl";
        var mtlPath = Path.Combine(Path.GetDirectoryName(path) ?? ".", mtlName);
        var materials = new Dictionary<string, (byte, byte, byte)>();
        var sb = new StringBuilder();
        sb.AppendLine(Header);
        bool anyColor = parts.Any(p => p.Color is not null);
        if (anyColor) sb.AppendLine($"mtllib {mtlName}");
        int offset = 1;
        int k = 0;
        foreach (var part in parts)
        {
            if (part.Vertices.Count == 0 || part.Triangles.Count == 0) continue;
            var name = Sanitize(part.Name, $"Parte_{++k}");
            sb.AppendLine($"o {name}");
            if (part.Color is { } c)
            {
                var mat = Sanitize(part.Material ?? $"cor_{c.R:X2}{c.G:X2}{c.B:X2}", $"cor_{c.R:X2}{c.G:X2}{c.B:X2}");
                materials[mat] = c;
                sb.AppendLine($"usemtl {mat}");
            }
            foreach (var v in part.Vertices)
                sb.Append("v ").Append(v.X.ToString("0.######", inv)).Append(' ')
                  .Append(v.Y.ToString("0.######", inv)).Append(' ')
                  .Append(v.Z.ToString("0.######", inv)).Append('\n');
            foreach (var t in part.Triangles)
                sb.Append("f ").Append(t.A + offset).Append(' ').Append(t.B + offset).Append(' ').Append(t.C + offset).Append('\n');
            offset += part.Vertices.Count;
        }
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(false));
        if (anyColor)
        {
            var m = new StringBuilder();
            foreach (var (name, (r, g, b)) in materials)
            {
                m.AppendLine($"newmtl {name}");
                m.AppendLine(string.Format(inv, "Kd {0:0.####} {1:0.####} {2:0.####}", r / 255.0, g / 255.0, b / 255.0));
            }
            File.WriteAllText(mtlPath, m.ToString(), new UTF8Encoding(false));
        }
    }

    private static string Sanitize(string name, string fallback)
    {
        var chars = name.Where(ch => !char.IsWhiteSpace(ch) && ch != '#').ToArray();
        var s = new string(chars);
        return string.IsNullOrEmpty(s) ? fallback : s;
    }
}
