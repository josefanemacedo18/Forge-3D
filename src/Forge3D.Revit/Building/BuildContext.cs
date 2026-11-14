using Autodesk.Revit.DB;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>Estado compartilhado durante a construção de uma família.</summary>
internal sealed class BuildContext
{
    public Document Doc { get; }
    public FamilySpec Spec { get; }
    public BuildReport Report { get; }
    public FamilyManager Mgr => Doc.FamilyManager;

    public ViewPlan Plan { get; private set; } = null!;
    public View Front { get; private set; } = null!;
    public View Side { get; private set; } = null!;
    public ReferencePlane CenterLR { get; private set; } = null!;
    public ReferencePlane CenterFB { get; private set; } = null!;
    public Level RefLevel { get; private set; } = null!;

    public Dictionary<string, ReferencePlane> Planes { get; } = new();
    public Dictionary<string, FamilyParameter> Params { get; } = new();
    public Dictionary<string, ElementId> Materials { get; } = new();
    public Dictionary<string, Element> Solids { get; } = new();

    /// <summary>Meia extensão das linhas dos planos (pés), com folga.</summary>
    public double PlaneHalfLength { get; }

    private readonly Dictionary<string, int> _dimLanes = new();

    public BuildContext(Document doc, FamilySpec spec, BuildReport report)
    {
        Doc = doc;
        Spec = spec;
        Report = report;
        var e = spec.Envelope;
        PlaneHalfLength = Mm.ToFt(Math.Max(Math.Max(e.Width, e.Depth), e.Height) / 2 + 300);
    }

    // -- vistas e referências raiz ----------------------------------------------------

    public void FindViewsAndRoots()
    {
        Plan = new FilteredElementCollector(Doc).OfClass(typeof(ViewPlan)).Cast<ViewPlan>()
                   .FirstOrDefault(v => !v.IsTemplate && v.ViewType == ViewType.FloorPlan)
               ?? throw new InvalidOperationException("O modelo de família não tem vista de planta (nível de referência).");
        var elevations = new FilteredElementCollector(Doc).OfClass(typeof(View)).Cast<View>()
            .Where(v => !v.IsTemplate && v.ViewType == ViewType.Elevation).ToList();
        if (elevations.Count == 0) throw new InvalidOperationException("O modelo de família não tem vistas de elevação.");
        Front = elevations.OrderByDescending(v => v.ViewDirection.DotProduct(-XYZ.BasisY)).First();
        Side = elevations.OrderByDescending(v => v.ViewDirection.DotProduct(XYZ.BasisX)).First();

        var planes = new FilteredElementCollector(Doc).OfClass(typeof(ReferencePlane)).Cast<ReferencePlane>().ToList();
        CenterLR = planes.FirstOrDefault(p => IsCenterPlane(p, XYZ.BasisX)) ?? CreateRootPlane(XYZ.BasisX, "Centro (Esq/Dir) Forge");
        CenterFB = planes.FirstOrDefault(p => IsCenterPlane(p, XYZ.BasisY)) ?? CreateRootPlane(XYZ.BasisY, "Centro (Frente/Trás) Forge");
        RefLevel = new FilteredElementCollector(Doc).OfClass(typeof(Level)).Cast<Level>()
                       .OrderBy(l => Math.Abs(l.Elevation)).FirstOrDefault()
                   ?? throw new InvalidOperationException("O modelo de família não tem nível de referência.");
    }

    private static bool IsCenterPlane(ReferencePlane p, XYZ normal)
    {
        if (Math.Abs(Math.Abs(p.Normal.DotProduct(normal)) - 1) > 1e-6) return false;
        var coord = p.BubbleEnd.DotProduct(normal);
        return Math.Abs(coord) < 1e-6;
    }

    private ReferencePlane CreateRootPlane(XYZ normal, string name)
    {
        var rp = CreatePlaneElement(normal.IsAlmostEqualTo(XYZ.BasisX) ? "x" : "y", 0.0);
        rp.Name = name;
        rp.Pinned = true;
        Report.Ok("Preparo", name, "plano central criado (o modelo não tinha)");
        return rp;
    }

    /// <summary>Ajusta a faixa de vista da planta para enxergar todo o objeto.</summary>
    public void ExpandPlanViewRange()
    {
        try
        {
            var range = Plan.GetViewRange();
            double h = Mm.ToFt(Spec.Envelope.Height);
            range.SetOffset(PlanViewPlane.TopClipPlane, h + Mm.ToFt(2000));
            range.SetOffset(PlanViewPlane.CutPlane, h + Mm.ToFt(1000));
            Plan.SetViewRange(range);
        }
        catch (Exception ex)
        {
            Report.Warnings.Add($"Faixa de vista da planta não ajustada: {ex.Message}");
        }
    }

    // -- planos -------------------------------------------------------------------------

    public ReferencePlane CreatePlaneElement(string axis, double positionFt)
    {
        double L = PlaneHalfLength;
        return axis switch
        {
            "x" => Doc.FamilyCreate.NewReferencePlane(new XYZ(positionFt, -L, 0), new XYZ(positionFt, L, 0), XYZ.BasisZ, Plan),
            "y" => Doc.FamilyCreate.NewReferencePlane(new XYZ(-L, positionFt, 0), new XYZ(L, positionFt, 0), XYZ.BasisZ, Plan),
            "z" => Doc.FamilyCreate.NewReferencePlane(new XYZ(-L, 0, positionFt), new XYZ(L, 0, positionFt), XYZ.BasisY, Front),
            _ => throw new ArgumentException($"Eixo inválido: {axis}"),
        };
    }

    public Reference PlaneRef(string id) => id switch
    {
        "@x0" => CenterLR.GetReference(),
        "@y0" => CenterFB.GetReference(),
        "@z0" => RefLevel.GetPlaneReference(),
        _ => Planes.TryGetValue(id, out var rp)
            ? rp.GetReference()
            : throw new InvalidOperationException($"Plano '{id}' não foi criado."),
    };

    public bool HasPlane(string id) => SpecMath.IsRoot(id) || Planes.ContainsKey(id);

    public string AxisOf(string id)
    {
        if (SpecMath.RootAxis.TryGetValue(id, out var a)) return a;
        return Spec.FindPlane(id)?.Axis ?? throw new InvalidOperationException($"Plano '{id}' inexistente no spec.");
    }

    public double PositionFt(string id) => SpecMath.IsRoot(id) ? 0.0 : Mm.ToFt(Spec.FindPlane(id)!.Position);

    /// <summary>Vista onde planos/faces com normal no eixo aparecem como linhas.</summary>
    public View ViewForNormalAxis(string axis) => axis == "z" ? Front : Plan;

    /// <summary>Vista onde um círculo perpendicular ao eixo aparece em verdadeira grandeza.</summary>
    public View ViewLookingAlong(string axis) => axis switch
    {
        "z" => Plan,
        "x" => Side,
        _ => Front,
    };

    /// <summary>Faixas paralelas para as linhas de cota não se sobreporem.</summary>
    public double NextDimensionOffsetFt(string axis)
    {
        _dimLanes.TryGetValue(axis, out var lane);
        _dimLanes[axis] = lane + 1;
        return Mm.ToFt(250 + 120 * lane);
    }

    public static int AxisIndex(string axis) => axis switch { "x" => 0, "y" => 1, "z" => 2, _ => throw new ArgumentException(axis) };

    public static XYZ AxisVector(string axis) => axis switch
    {
        "x" => XYZ.BasisX,
        "y" => XYZ.BasisY,
        "z" => XYZ.BasisZ,
        _ => throw new ArgumentException(axis),
    };

    public static double Component(XYZ p, int index) => index switch { 0 => p.X, 1 => p.Y, _ => p.Z };
}
