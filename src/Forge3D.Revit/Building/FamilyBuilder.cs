using System.Diagnostics;
using Autodesk.Revit.DB;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;
using RevitApplication = Autodesk.Revit.ApplicationServices.Application;

namespace Forge3D.Revit.Building;

public sealed class BuildOptions
{
    public required string TemplatePath { get; init; }
    public required string OutputPath { get; init; }
    public bool RunFlexTest { get; init; } = true;
    public Action<double, string>? Progress { get; init; }
}

public sealed record BuildResult(string FamilyPath, BuildReport Report);

/// <summary>
/// Constrói a família do Revit a partir do FamilySpec. Cada operação
/// arriscada roda em transação própria: se falhar, só ela é desfeita e o
/// relatório registra o motivo. A família nunca fica pela metade.
/// </summary>
public sealed class FamilyBuilder
{
    private readonly RevitApplication _app;

    public FamilyBuilder(RevitApplication app) => _app = app;

    public BuildResult Build(FamilySpec spec, BuildOptions options)
    {
        var errors = SpecValidator.Validate(spec);
        if (errors.Count > 0) throw new SpecException("O FamilySpec é inválido; nada foi criado no Revit.", errors);
        var sw = Stopwatch.StartNew();
        var report = new BuildReport(spec);
        var progress = options.Progress ?? ((_, _) => { });
        Log.Info($"Construindo família '{spec.Family.Name}' a partir do modelo '{options.TemplatePath}'.");

        progress(0.02, "Criando o documento da família");
        Document doc = _app.NewFamilyDocument(options.TemplatePath)
                       ?? throw new InvalidOperationException("O Revit não criou o documento da família.");
        try
        {
            var ctx = new BuildContext(doc, spec, report);
            Prepare(ctx);
            progress(0.15, "Criando planos de referência");
            CreatePlanes(ctx);
            progress(0.25, "Criando cotas e rótulos");
            CreatePlaneDimensions(ctx);
            progress(0.35, "Aplicando fórmulas");
            ApplyFormulas(ctx);
            progress(0.4, "Criando a geometria");
            var solids = new SolidBuilder(ctx);
            int k = 0;
            foreach (var s in spec.Solids)
            {
                progress(0.4 + 0.35 * k++ / Math.Max(1, spec.Solids.Count), $"Criando '{s.Name}'");
                solids.Build(s);
            }
            progress(0.76, "Recortes e materiais");
            solids.ApplyVoidCuts();
            solids.AssociateMaterials();
            CreateTypes(ctx);
            progress(0.8, "Conferindo o envelope");
            CheckEnvelope(ctx);
            if (options.RunFlexTest && spec.Planes.Count > 0)
            {
                progress(0.82, "Teste de flexão");
                new FlexTester(ctx).Run((f, m) => progress(0.82 + 0.13 * f, m));
            }
            progress(0.96, "Salvando a família");
            Directory.CreateDirectory(Path.GetDirectoryName(options.OutputPath)!);
            doc.SaveAs(options.OutputPath, new SaveAsOptions { OverwriteExistingFile = true, MaximumBackups = 1 });
            report.FamilyPath = options.OutputPath;
            report.Duration = sw.Elapsed;
            Log.Info($"Família salva em '{options.OutputPath}' (nível efetivo {report.EffectiveLevel}).");
            progress(1.0, "Família criada");
            return new BuildResult(options.OutputPath, report);
        }
        finally
        {
            try
            {
                doc.Close(false);
            }
            catch (Exception ex)
            {
                Log.Warn($"Falha ao fechar o documento da família: {ex.Message}");
            }
        }
    }

    // -- preparo: categoria, vistas, materiais e parâmetros --------------------------------

    private static void Prepare(BuildContext ctx)
    {
        var r = Tx.Run(ctx.Doc, "Forge 3D - preparar família", () =>
        {
            SetCategory(ctx);
            ctx.FindViewsAndRoots();
            ctx.ExpandPlanViewRange();
            CreateMaterials(ctx);
            CreateParameters(ctx);
        });
        if (!r.Ok) throw new InvalidOperationException("Não foi possível preparar a família: " + r.ErrorText);
        ctx.Report.Warnings.AddRange(r.Warnings);
    }

    private static readonly Dictionary<string, BuiltInCategory> CategoryMap = new()
    {
        ["GenericModel"] = BuiltInCategory.OST_GenericModel,
        ["Furniture"] = BuiltInCategory.OST_Furniture,
        ["FurnitureSystems"] = BuiltInCategory.OST_FurnitureSystems,
        ["Casework"] = BuiltInCategory.OST_Casework,
        ["LightingFixtures"] = BuiltInCategory.OST_LightingFixtures,
        ["Planting"] = BuiltInCategory.OST_Planting,
        ["Entourage"] = BuiltInCategory.OST_Entourage,
        ["SpecialtyEquipment"] = BuiltInCategory.OST_SpecialityEquipment,
        ["PlumbingFixtures"] = BuiltInCategory.OST_PlumbingFixtures,
        ["Site"] = BuiltInCategory.OST_Site,
        ["Parking"] = BuiltInCategory.OST_Parking,
        ["MechanicalEquipment"] = BuiltInCategory.OST_MechanicalEquipment,
        ["ElectricalEquipment"] = BuiltInCategory.OST_ElectricalEquipment,
        ["ElectricalFixtures"] = BuiltInCategory.OST_ElectricalFixtures,
        ["FoodServiceEquipment"] = BuiltInCategory.OST_FoodServiceEquipment,
        ["MedicalEquipment"] = BuiltInCategory.OST_MedicalEquipment,
        ["Signage"] = BuiltInCategory.OST_Signage,
    };

    private static void SetCategory(BuildContext ctx)
    {
        var name = ctx.Spec.Family.Category;
        if (!CategoryMap.TryGetValue(name, out var bic)) bic = BuiltInCategory.OST_GenericModel;
        try
        {
            var cat = Category.GetCategory(ctx.Doc, bic);
            if (cat is null) throw new InvalidOperationException("categoria indisponível neste documento");
            ctx.Doc.OwnerFamily.FamilyCategory = cat;
            ctx.Report.Ok("Preparo", "Categoria", cat.Name);
        }
        catch (Exception ex)
        {
            ctx.Report.Fail("Preparo", "Categoria", $"'{name}' não pôde ser aplicada ({ex.Message}); mantida Modelo genérico.");
        }
    }

    private static void CreateMaterials(BuildContext ctx)
    {
        var existing = new FilteredElementCollector(ctx.Doc).OfClass(typeof(Material)).Cast<Material>()
            .GroupBy(m => m.Name).ToDictionary(g => g.Key, g => g.First());
        foreach (var m in ctx.Spec.Materials)
        {
            try
            {
                Material mat;
                if (existing.TryGetValue(m.Name, out var found)) mat = found;
                else mat = (Material)ctx.Doc.GetElement(Material.Create(ctx.Doc, m.Name));
                mat.Color = new Color((byte)m.Color[0], (byte)m.Color[1], (byte)m.Color[2]);
                if (m.Transparency is { } tr) mat.Transparency = (int)Math.Clamp(tr, 0, 100);
                if (m.Shininess is { } sh) mat.Shininess = Math.Clamp(sh, 0, 128);
                if (m.Smoothness is { } sm) mat.Smoothness = Math.Clamp(sm, 0, 100);
                ctx.Materials[m.Id] = mat.Id;
            }
            catch (Exception ex)
            {
                ctx.Report.Fail("Materiais", m.Name, ex.Message);
            }
        }
        if (ctx.Spec.Materials.Count > 0) ctx.Report.Ok("Materiais", $"{ctx.Materials.Count} materiais");
    }

    private static ForgeTypeId Group(string group) => group switch
    {
        "dimensions" => GroupTypeId.Geometry,
        "constraints" => GroupTypeId.Constraints,
        "materials" => GroupTypeId.Materials,
        "visibility" => GroupTypeId.Visibility,
        "identity" => GroupTypeId.IdentityData,
        _ => GroupTypeId.General,
    };

    private static ForgeTypeId SpecType(string kind) => kind switch
    {
        ParameterKinds.Length => SpecTypeId.Length,
        ParameterKinds.Angle => SpecTypeId.Angle,
        ParameterKinds.Number => SpecTypeId.Number,
        ParameterKinds.Integer => SpecTypeId.Int.Integer,
        ParameterKinds.YesNo => SpecTypeId.Boolean.YesNo,
        ParameterKinds.Material => SpecTypeId.Reference.Material,
        _ => SpecTypeId.String.Text,
    };

    private static void CreateParameters(BuildContext ctx)
    {
        var mgr = ctx.Mgr;
        var typeName = ctx.Spec.Types.FirstOrDefault()?.Name ?? "Padrão";
        if (mgr.CurrentType is null)
            mgr.NewType(typeName);
        else
        {
            try { mgr.RenameCurrentType(typeName); } catch { /* nome em uso: mantém */ }
        }

        foreach (var p in ctx.Spec.Parameters)
        {
            try
            {
                var existing = mgr.get_Parameter(p.Name);
                FamilyParameter fp = existing ?? mgr.AddParameter(p.Name, Group(p.Group), SpecType(p.Kind), p.Instance);
                if (!string.IsNullOrWhiteSpace(p.Description))
                {
                    try { mgr.SetDescription(fp, p.Description); } catch { /* descrição é opcional */ }
                }
                ctx.Params[p.Id] = fp;
                SetValue(ctx, p, fp);
            }
            catch (Exception ex)
            {
                ctx.Report.Fail("Parâmetros", p.Name, ex.Message);
            }
        }
        ctx.Report.Ok("Parâmetros", $"{ctx.Params.Count} de {ctx.Spec.Parameters.Count} criados");
    }

    private static void SetValue(BuildContext ctx, ParameterSpec p, FamilyParameter fp)
    {
        if (p.HasFormula) return;
        var mgr = ctx.Mgr;
        switch (p.Kind)
        {
            case ParameterKinds.Length:
                mgr.Set(fp, Mm.ToFt(p.NumberValue));
                break;
            case ParameterKinds.Angle:
                mgr.Set(fp, p.NumberValue * Math.PI / 180.0);
                break;
            case ParameterKinds.Number:
                mgr.Set(fp, p.NumberValue);
                break;
            case ParameterKinds.Integer:
                mgr.Set(fp, (int)Math.Round(p.NumberValue));
                break;
            case ParameterKinds.YesNo:
                mgr.Set(fp, p.BoolValue ? 1 : 0);
                break;
            case ParameterKinds.Material:
                if (p.StringValue is { } mid && ctx.Materials.TryGetValue(mid, out var matId)) mgr.Set(fp, matId);
                break;
            case ParameterKinds.Text:
                mgr.Set(fp, p.StringValue ?? "");
                break;
        }
    }

    // -- planos e cotas ------------------------------------------------------------------

    private static readonly Dictionary<string, FamilyInstanceReferenceType> ReferenceTypes = new()
    {
        ["left"] = FamilyInstanceReferenceType.Left,
        ["right"] = FamilyInstanceReferenceType.Right,
        ["front"] = FamilyInstanceReferenceType.Front,
        ["back"] = FamilyInstanceReferenceType.Back,
        ["top"] = FamilyInstanceReferenceType.Top,
        ["bottom"] = FamilyInstanceReferenceType.Bottom,
        ["strong"] = FamilyInstanceReferenceType.StrongReference,
        ["weak"] = FamilyInstanceReferenceType.WeakReference,
        ["none"] = FamilyInstanceReferenceType.NotAReference,
    };

    private static void CreatePlanes(BuildContext ctx)
    {
        var existingNames = new FilteredElementCollector(ctx.Doc).OfClass(typeof(ReferencePlane)).Cast<ReferencePlane>()
            .Select(p => p.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var r = Tx.Run(ctx.Doc, "Forge 3D - planos de referência", () =>
        {
            foreach (var pl in TopologicalPlanes(ctx.Spec))
            {
                var rp = ctx.CreatePlaneElement(pl.Axis, Mm.ToFt(pl.Position));
                var name = pl.Name;
                for (int k = 2; existingNames.Contains(name); k++) name = $"{pl.Name} {k}";
                try { rp.Name = name; existingNames.Add(name); } catch { /* nome rejeitado: fica o padrão */ }
                if (ReferenceTypes.TryGetValue(pl.Reference, out var refType))
                {
                    try { rp.get_Parameter(BuiltInParameter.ELEM_REFERENCE_NAME)?.Set((int)refType); } catch { /* opcional */ }
                }
                ctx.Planes[pl.Id] = rp;
            }
        });
        if (!r.Ok) throw new InvalidOperationException("Não foi possível criar os planos de referência: " + r.ErrorText);
        foreach (var pl in ctx.Spec.Planes)
        {
            var rp = ctx.Planes[pl.Id];
            var normal = BuildContext.AxisVector(pl.Axis);
            if (Math.Abs(Math.Abs(rp.Normal.DotProduct(normal)) - 1) > 1e-6)
                ctx.Report.Fail("Planos", pl.Name, "plano criado com orientação inesperada");
        }
        ctx.Report.Ok("Planos", $"{ctx.Planes.Count} planos de referência");
    }

    internal static List<PlaneSpec> TopologicalPlanes(FamilySpec spec)
    {
        var ordered = new List<PlaneSpec>();
        var done = new HashSet<string>(SpecMath.RootAxis.Keys);
        var pending = spec.Planes.ToList();
        while (pending.Count > 0)
        {
            var ready = pending.Where(p => done.Contains(p.Parent)).ToList();
            if (ready.Count == 0) throw new SpecException("Árvore de planos com ciclo ou pai inexistente.");
            foreach (var p in ready)
            {
                ordered.Add(p);
                done.Add(p.Id);
                pending.Remove(p);
            }
        }
        return ordered;
    }

    private static void CreatePlaneDimensions(BuildContext ctx)
    {
        foreach (var pl in TopologicalPlanes(ctx.Spec))
        {
            if (!ctx.Params.TryGetValue(pl.Driver, out var fp))
            {
                ctx.Report.Fail("Cotas", pl.Name, "parâmetro da cota não foi criado");
                continue;
            }
            var r = Tx.Run(ctx.Doc, $"Forge 3D - cota {pl.Name}", () =>
            {
                var dim = Dimensions.Linear(ctx, pl.Axis, ctx.PlaneRef(pl.Parent), ctx.PositionFt(pl.Parent),
                    ctx.PlaneRef(pl.Id), Mm.ToFt(pl.Position));
                dim.FamilyLabel = fp;
            });
            if (r.Ok) ctx.Report.Ok("Cotas", pl.Name, fp.Definition.Name);
            else ctx.Report.Fail("Cotas", pl.Name, r.ErrorText);
        }
    }

    private static void ApplyFormulas(BuildContext ctx)
    {
        foreach (var p in ctx.Spec.Parameters.Where(p => p.HasFormula))
        {
            if (!ctx.Params.TryGetValue(p.Id, out var fp)) continue;
            var r = Tx.Run(ctx.Doc, $"Forge 3D - fórmula {p.Name}", () => ctx.Mgr.SetFormula(fp, p.Formula));
            if (r.Ok) ctx.Report.Ok("Fórmulas", p.Name, p.Formula!);
            else ctx.Report.Fail("Fórmulas", p.Name, r.ErrorText);
        }
    }

    // -- tipos e conferência -----------------------------------------------------------------

    private static void CreateTypes(BuildContext ctx)
    {
        var types = ctx.Spec.Types;
        if (types.Count <= 1) return;
        foreach (var t in types.Skip(1))
        {
            var r = Tx.Run(ctx.Doc, $"Forge 3D - tipo {t.Name}", () =>
            {
                ctx.Mgr.NewType(t.Name);
                foreach (var (pid, value) in t.Values)
                {
                    if (!ctx.Params.TryGetValue(pid, out var fp)) continue;
                    var p = ctx.Spec.FindParameter(pid)!;
                    if (p.Kind == ParameterKinds.Length && value.ValueKind == System.Text.Json.JsonValueKind.Number)
                        ctx.Mgr.Set(fp, Mm.ToFt(value.GetDouble()));
                }
            });
            if (r.Ok) ctx.Report.Ok("Tipos", t.Name);
            else ctx.Report.Fail("Tipos", t.Name, r.ErrorText);
        }
    }

    private static void CheckEnvelope(BuildContext ctx)
    {
        var bb = Geo.ModelBounds(ctx.Doc, ctx.Spec.Solids.Where(s => !s.IsVoid).SelectMany(s => ctx.ElementsOf(s.Id)));
        if (bb is null)
        {
            ctx.Report.EnvelopeOk = false;
            ctx.Report.EnvelopeDetail = "(nenhum sólido foi criado)";
            return;
        }
        var e = ctx.Spec.Envelope;
        double w = Mm.FromFt(bb.Max.X - bb.Min.X), d = Mm.FromFt(bb.Max.Y - bb.Min.Y), h = Mm.FromFt(bb.Max.Z - bb.Min.Z);
        double tol = Math.Max(2.0, 0.005 * Math.Max(Math.Max(e.Width, e.Depth), e.Height));
        ctx.Report.EnvelopeOk = Math.Abs(w - e.Width) <= tol && Math.Abs(d - e.Depth) <= tol && Math.Abs(h - e.Height) <= tol;
        ctx.Report.EnvelopeDetail = $"({w:0.#} x {d:0.#} x {h:0.#} mm; esperado {e.Width:0.#} x {e.Depth:0.#} x {e.Height:0.#} mm)";
    }
}
