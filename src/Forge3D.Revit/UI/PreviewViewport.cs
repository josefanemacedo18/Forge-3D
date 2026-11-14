using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Media3D;

namespace Forge3D.Revit.UI;

/// <summary>
/// Visualizador 3D simples da prévia: arraste para girar, roda para zoom,
/// duplo clique para reenquadrar. Cores indicam o nível de cada peça.
/// </summary>
public sealed class PreviewViewport : Border
{
    private readonly Viewport3D _viewport = new() { ClipToBounds = true };
    private readonly PerspectiveCamera _camera = new() { FieldOfView = 40 };
    private readonly ModelVisual3D _content = new();
    private Point3D _target = new(0, 0, 0);
    private double _radius = 3000, _yaw = -55, _pitch = 25;
    private Point? _drag;

    public PreviewViewport()
    {
        Background = new LinearGradientBrush(Color.FromRgb(0xFA, 0xFB, 0xFC), Color.FromRgb(0xE4, 0xE8, 0xEE), 90);
        BorderBrush = new SolidColorBrush(Color.FromRgb(0xD9, 0xDD, 0xE3));
        BorderThickness = new Thickness(1);
        _viewport.Camera = _camera;
        var lights = new Model3DGroup();
        lights.Children.Add(new AmbientLight(Color.FromRgb(0x60, 0x60, 0x60)));
        lights.Children.Add(new DirectionalLight(Color.FromRgb(0xC8, 0xC8, 0xC8), new Vector3D(-0.4, -0.6, -1)));
        lights.Children.Add(new DirectionalLight(Color.FromRgb(0x50, 0x50, 0x50), new Vector3D(0.6, 0.5, 0.3)));
        _viewport.Children.Add(new ModelVisual3D { Content = lights });
        _viewport.Children.Add(_content);
        Child = _viewport;
        MouseLeftButtonDown += (_, e) =>
        {
            if (e.ClickCount == 2) { Frame(); return; }
            _drag = e.GetPosition(this);
            CaptureMouse();
        };
        MouseLeftButtonUp += (_, _) => { _drag = null; ReleaseMouseCapture(); };
        MouseMove += (_, e) =>
        {
            if (_drag is not { } start) return;
            var p = e.GetPosition(this);
            _yaw -= (p.X - start.X) * 0.4;
            _pitch = Math.Clamp(_pitch + (p.Y - start.Y) * 0.4, -85, 85);
            _drag = p;
            UpdateCamera();
        };
        MouseWheel += (_, e) =>
        {
            _radius *= e.Delta > 0 ? 0.88 : 1.14;
            UpdateCamera();
        };
    }

    private Rect3D _bounds = Rect3D.Empty;

    public void Clear()
    {
        _content.Content = null;
        _bounds = Rect3D.Empty;
    }

    /// <summary>Carrega o OBJ de prévia do motor; <paramref name="colorOf"/> dá a cor de cada objeto.</summary>
    public void LoadObj(string path, Func<string, Color> colorOf)
    {
        var group = new Model3DGroup();
        var positions = new List<Point3D>();
        string current = "";
        var faces = new Dictionary<string, List<int>>();
        foreach (var raw in File.ReadLines(path))
        {
            var line = raw.Trim();
            if (line.StartsWith("o ")) { current = line[2..].Trim(); continue; }
            if (line.StartsWith("v "))
            {
                var p = line[2..].Split(' ', StringSplitOptions.RemoveEmptyEntries);
                positions.Add(new Point3D(Num(p[0]), Num(p[1]), Num(p[2])));
            }
            else if (line.StartsWith("f "))
            {
                var p = line[2..].Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (!faces.TryGetValue(current, out var list)) faces[current] = list = new List<int>();
                for (int i = 1; i + 1 < p.Length; i++)
                {
                    list.Add(Index(p[0]));
                    list.Add(Index(p[i]));
                    list.Add(Index(p[i + 1]));
                }
            }
        }
        foreach (var (name, idx) in faces)
        {
            var mesh = new MeshGeometry3D();
            // Vértices por triângulo: normais planas (aparência de sólido facetado).
            for (int i = 0; i + 2 < idx.Count; i += 3)
            {
                var a = positions[idx[i]];
                var b = positions[idx[i + 1]];
                var c = positions[idx[i + 2]];
                var n = Vector3D.CrossProduct(b - a, c - a);
                if (n.Length < 1e-12) continue;
                n.Normalize();
                int k = mesh.Positions.Count;
                mesh.Positions.Add(a); mesh.Positions.Add(b); mesh.Positions.Add(c);
                mesh.Normals.Add(n); mesh.Normals.Add(n); mesh.Normals.Add(n);
                mesh.TriangleIndices.Add(k); mesh.TriangleIndices.Add(k + 1); mesh.TriangleIndices.Add(k + 2);
            }
            var brush = new SolidColorBrush(colorOf(name));
            brush.Freeze();
            var material = new MaterialGroup();
            material.Children.Add(new DiffuseMaterial(brush));
            material.Children.Add(new SpecularMaterial(new SolidColorBrush(Color.FromArgb(60, 255, 255, 255)), 30));
            group.Children.Add(new GeometryModel3D(mesh, material) { BackMaterial = new DiffuseMaterial(brush) });
        }
        group.Freeze();
        _content.Content = group;
        _bounds = group.Bounds;
        Frame();

        int Index(string token)
        {
            var s = token.Split('/')[0];
            int i = int.Parse(s, CultureInfo.InvariantCulture);
            return i > 0 ? i - 1 : positions.Count + i;
        }
    }

    private static double Num(string s) => double.Parse(s, CultureInfo.InvariantCulture);

    public void Frame()
    {
        if (_bounds.IsEmpty) return;
        _target = new Point3D(_bounds.X + _bounds.SizeX / 2, _bounds.Y + _bounds.SizeY / 2, _bounds.Z + _bounds.SizeZ / 2);
        var diag = Math.Sqrt(_bounds.SizeX * _bounds.SizeX + _bounds.SizeY * _bounds.SizeY + _bounds.SizeZ * _bounds.SizeZ);
        _radius = Math.Max(diag * 1.6, 1);
        _yaw = -55;
        _pitch = 25;
        UpdateCamera();
    }

    private void UpdateCamera()
    {
        double yaw = _yaw * Math.PI / 180, pitch = _pitch * Math.PI / 180;
        var dir = new Vector3D(Math.Cos(pitch) * Math.Cos(yaw), Math.Cos(pitch) * Math.Sin(yaw), Math.Sin(pitch));
        _camera.Position = _target + dir * _radius;
        _camera.LookDirection = -dir * _radius;
        _camera.UpDirection = new Vector3D(0, 0, 1);
        _camera.NearPlaneDistance = Math.Max(_radius / 1000, 0.1);
        _camera.FarPlaneDistance = _radius * 20;
    }
}
