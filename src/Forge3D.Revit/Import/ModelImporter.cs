using Autodesk.Revit.DB;
using Forge3D.Core.Settings;
using Forge3D.Revit.Infrastructure;
using RevitApplication = Autodesk.Revit.ApplicationServices.Application;

namespace Forge3D.Revit.Import;

/// <summary>Arquivo pronto para o motor e como ele foi obtido.</summary>
public sealed record PreparedModel(string MeshPath, string Route, IReadOnlyList<string> Notes);

/// <summary>
/// Decide a rota de leitura de cada formato:
/// - OBJ, STL, PLY, OFF, GLB/GLTF, DAE, 3MF: direto no motor;
/// - SKP, STEP, 3DM, SAT: conversores do Revit (sem abrir outro programa);
/// - MAX, FBX, 3DS: 3ds Max em modo lote.
/// </summary>
public static class ModelImporter
{
    public static readonly HashSet<string> EngineNative = new(StringComparer.OrdinalIgnoreCase)
        { ".obj", ".stl", ".ply", ".off", ".glb", ".gltf", ".dae", ".3mf" };

    public static readonly HashSet<string> ViaRevit = new(StringComparer.OrdinalIgnoreCase)
        { ".skp", ".stp", ".step", ".3dm", ".sat" };

    public static readonly HashSet<string> Via3dsMax = new(StringComparer.OrdinalIgnoreCase)
        { ".max", ".fbx", ".3ds" };

    /// <summary>
    /// Prepara o arquivo. Deve ser chamado na thread da API do Revit (as rotas
    /// pelo Revit usam o documento ativo); a rota do 3ds Max roda em processo externo.
    /// </summary>
    public static PreparedModel Prepare(RevitApplication app, Document? doc, string path, Forge3DSettings settings,
        string templatePath, string workDir, CancellationToken ct)
    {
        if (!File.Exists(path)) throw new FileNotFoundException($"Arquivo não encontrado: {path}", path);
        Directory.CreateDirectory(workDir);
        var ext = Path.GetExtension(path);
        var obj = Path.Combine(workDir, Path.GetFileNameWithoutExtension(path) + "_forge3d.obj");
        var notes = new List<string>();

        if (EngineNative.Contains(ext)) return new PreparedModel(path, "motor", notes);

        if (ViaRevit.Contains(ext))
        {
            try
            {
                int n;
                if (doc is not null && !doc.IsReadOnly)
                    n = RevitShapeReader.ExportWithShapeImporter(doc, path, obj);
                else
                {
                    // Sem documento editável aberto: usa um documento temporário.
                    var temp = app.NewFamilyDocument(templatePath) ?? throw new InvalidOperationException("documento temporário não criado");
                    try { n = RevitShapeReader.ExportWithShapeImporter(temp, path, obj); }
                    finally { temp.Close(false); }
                }
                notes.Add($"Lido pelo conversor do Revit ({n} objeto(s)).");
                return new PreparedModel(obj, "revit", notes);
            }
            catch (Exception ex) when (ext.Equals(".skp", StringComparison.OrdinalIgnoreCase) || ext.Equals(".sat", StringComparison.OrdinalIgnoreCase))
            {
                Log.Warn($"ShapeImporter falhou para '{path}': {ex.Message}. Tentando importação com grupos.");
                notes.Add($"Conversor direto falhou ({ex.Message}); usada a importação do Revit.");
                try
                {
                    int n = RevitShapeReader.ExportWithImport(app, templatePath, path, obj);
                    notes.Add($"Importado pelo Revit ({n} objeto(s)).");
                    return new PreparedModel(obj, "revit-importacao", notes);
                }
                catch (Exception ex2) when (ext.Equals(".skp", StringComparison.OrdinalIgnoreCase))
                {
                    var su = ExternalExporters.FindSketchUp(settings)
                             ?? throw new InvalidOperationException(
                                 $"O Revit não conseguiu ler este arquivo do SketchUp ({ex2.Message}) e o SketchUp não foi encontrado. " +
                                 "Salve o arquivo numa versão anterior do SketchUp ou exporte em OBJ/DAE.");
                    ExternalExporters.ExportWithSketchUp(su, path, obj, ct);
                    notes.Add("Exportado pelo SketchUp instalado.");
                    return new PreparedModel(obj, "sketchup", notes);
                }
            }
        }

        if (Via3dsMax.Contains(ext))
        {
            var batch = ExternalExporters.Find3dsMaxBatch(settings)
                        ?? throw new InvalidOperationException(
                            "3ds Max não encontrado (3dsmaxbatch.exe). Informe o caminho em Configurações ou exporte o modelo em OBJ.");
            ExternalExporters.ExportWith3dsMax(batch, path, obj, ct);
            notes.Add("Exportado pelo 3ds Max em modo lote.");
            return new PreparedModel(obj, "3dsmax", notes);
        }

        throw new InvalidOperationException($"Formato '{ext}' não suportado.");
    }

    public static bool IsSupported(string path)
    {
        var ext = Path.GetExtension(path);
        return EngineNative.Contains(ext) || ViaRevit.Contains(ext) || Via3dsMax.Contains(ext);
    }
}
