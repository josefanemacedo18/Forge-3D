using Autodesk.Revit.DB;
using Forge3D.Core.Mesh;
using Forge3D.Revit.Infrastructure;
using RevitApplication = Autodesk.Revit.ApplicationServices.Application;

namespace Forge3D.Revit.Import;

/// <summary>
/// Lê SketchUp, STEP, Rhino e SAT com os conversores do próprio Revit e grava
/// um OBJ (mm, Z para cima) para o motor. Nada é adicionado ao projeto do
/// usuário: a leitura acontece numa transação desfeita ou num documento
/// temporário que é fechado sem salvar.
/// </summary>
internal static class RevitShapeReader
{
    public static int ExportWithShapeImporter(Document doc, string sourcePath, string objPath)
    {
        if (!ShapeImporter.IsServiceAvailable())
            throw new InvalidOperationException("O serviço de conversão de formatos do Revit não está disponível nesta instalação.");
        var parts = new List<MeshPart>();
        using (var t = new Transaction(doc, "Forge 3D - leitura temporária"))
        {
            t.Start();
            try
            {
                using var importer = new ShapeImporter();
                var objects = importer.Convert(doc, sourcePath);
                int k = 0;
                foreach (var obj in objects)
                    Collect(doc, obj, Transform.Identity, $"Objeto {++k}", parts);
            }
            finally
            {
                t.RollBack();
            }
        }
        if (parts.Count == 0) throw new InvalidOperationException("O arquivo não contém geometria 3D legível pelo Revit.");
        ObjWriter.Write(objPath, parts);
        return parts.Count;
    }

    /// <summary>
    /// Alternativa para SketchUp: importa num documento de família temporário,
    /// preservando grupos e componentes como partes separadas.
    /// </summary>
    public static int ExportWithImport(RevitApplication app, string templatePath, string sourcePath, string objPath)
    {
        var temp = app.NewFamilyDocument(templatePath) ?? throw new InvalidOperationException("documento temporário não criado");
        try
        {
            var view = new FilteredElementCollector(temp).OfClass(typeof(ViewPlan)).Cast<ViewPlan>().First(v => !v.IsTemplate);
            var parts = new List<MeshPart>();
            using (var t = new Transaction(temp, "Forge 3D - importar"))
            {
                t.Start();
                ElementId id = Path.GetExtension(sourcePath).ToLowerInvariant() switch
                {
                    ".skp" => temp.Import(sourcePath, new SKPImportOptions(), view),
                    ".sat" => temp.Import(sourcePath, new SATImportOptions(), view),
                    _ => throw new InvalidOperationException("formato não suportado pela importação do Revit"),
                };
                t.Commit();
                var inst = temp.GetElement(id) ?? throw new InvalidOperationException("importação não criou elementos");
                var geom = inst.get_Geometry(new Options { DetailLevel = ViewDetailLevel.Fine });
                int k = 0;
                foreach (var obj in geom)
                    Collect(temp, obj, Transform.Identity, $"Objeto {++k}", parts);
            }
            if (parts.Count == 0) throw new InvalidOperationException("A importação não produziu geometria 3D.");
            ObjWriter.Write(objPath, parts);
            return parts.Count;
        }
        finally
        {
            temp.Close(false);
        }
    }

    private static void Collect(Document doc, GeometryObject obj, Transform tr, string name, List<MeshPart> parts)
    {
        switch (obj)
        {
            case Solid solid when solid.Faces.Size > 0:
                AddSolid(doc, solid, tr, name, parts);
                break;
            case Autodesk.Revit.DB.Mesh mesh when mesh.NumTriangles > 0:
                AddMesh(doc, mesh, tr, name, parts);
                break;
            case GeometryInstance gi:
            {
                var inner = tr.Multiply(gi.Transform);
                var symbolName = doc.GetElement(gi.GetSymbolGeometryId().SymbolId)?.Name;
                int k = 0;
                foreach (var child in gi.GetSymbolGeometry())
                    Collect(doc, child, inner, string.IsNullOrWhiteSpace(symbolName) ? $"{name}.{++k}" : $"{symbolName} {++k}", parts);
                break;
            }
            case GeometryElement ge:
            {
                int k = 0;
                foreach (var child in ge) Collect(doc, child, tr, $"{name}.{++k}", parts);
                break;
            }
        }
    }

    private static (byte, byte, byte)? MaterialColor(Document doc, ElementId id)
    {
        if (id is null || id == ElementId.InvalidElementId) return null;
        if (doc.GetElement(id) is Material m && m.Color.IsValid) return (m.Color.Red, m.Color.Green, m.Color.Blue);
        return null;
    }

    private static void AddSolid(Document doc, Solid solid, Transform tr, string name, List<MeshPart> parts)
    {
        (byte, byte, byte)? color = null;
        var part = new MeshPart { Name = name };
        foreach (Face face in solid.Faces)
        {
            color ??= MaterialColor(doc, face.MaterialElementId);
            var mesh = face.Triangulate(0.5);
            if (mesh is null) continue;
            AppendMesh(part, mesh, tr);
        }
        if (part.Triangles.Count == 0) return;
        parts.Add(color is null ? part : Recolor(part, color.Value));
    }

    private static void AddMesh(Document doc, Autodesk.Revit.DB.Mesh mesh, Transform tr, string name, List<MeshPart> parts)
    {
        var part = new MeshPart { Name = name };
        AppendMesh(part, mesh, tr);
        var color = MaterialColor(doc, mesh.MaterialElementId);
        if (part.Triangles.Count > 0) parts.Add(color is null ? part : Recolor(part, color.Value));
    }

    private static void AppendMesh(MeshPart part, Autodesk.Revit.DB.Mesh mesh, Transform tr)
    {
        int baseIndex = part.Vertices.Count;
        foreach (var v in mesh.Vertices)
        {
            var p = tr.OfPoint(v);
            part.Vertices.Add((Mm.FromFt(p.X), Mm.FromFt(p.Y), Mm.FromFt(p.Z)));
        }
        for (int i = 0; i < mesh.NumTriangles; i++)
        {
            var t = mesh.get_Triangle(i);
            part.Triangles.Add(((int)t.get_Index(0) + baseIndex, (int)t.get_Index(1) + baseIndex, (int)t.get_Index(2) + baseIndex));
        }
    }

    private static MeshPart Recolor(MeshPart p, (byte, byte, byte) c)
    {
        var q = new MeshPart { Name = p.Name, Color = c };
        q.Vertices.AddRange(p.Vertices);
        q.Triangles.AddRange(p.Triangles);
        return q;
    }
}
