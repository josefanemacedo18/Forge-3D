using Autodesk.Revit.DB;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>Conversões de perfis do spec em curvas do Revit e busca de faces.</summary>
internal static class Geo
{
    /// <summary>Ponto 3D de uma coordenada (u, v) de perfil no plano perpendicular ao eixo.</summary>
    public static XYZ AxisPoint(string axis, double u, double v, double w = 0) => axis switch
    {
        "z" => Mm.Xyz(u, v, w),
        "x" => Mm.Xyz(w, u, v),
        "y" => Mm.Xyz(u, w, v),
        _ => throw new ArgumentException(axis),
    };

    /// <summary>Plano de esboço perpendicular ao eixo, passando pela origem.</summary>
    public static Plane AxisPlane(string axis) => Plane.CreateByNormalAndOrigin(BuildContext.AxisVector(axis), XYZ.Zero);

    public static Curve Segment(SegmentSpec s, Func<double, double, XYZ> map)
    {
        var a = map(s.Start[0], s.Start[1]);
        var b = map(s.End[0], s.End[1]);
        if (s.IsArc && s.Mid is { Length: 2 } m)
        {
            var mid = map(m[0], m[1]);
            return Arc.Create(a, b, mid);
        }
        return Line.CreateBound(a, b);
    }

    public static CurveArray Loop(IEnumerable<SegmentSpec> loop, Func<double, double, XYZ> map)
    {
        var arr = new CurveArray();
        foreach (var s in loop) arr.Append(Segment(s, map));
        return arr;
    }

    public static CurveArrArray Loops(IEnumerable<IEnumerable<SegmentSpec>> loops, Func<double, double, XYZ> map)
    {
        var arr = new CurveArrArray();
        foreach (var lp in loops) arr.Append(Loop(lp, map));
        return arr;
    }

    /// <summary>Círculo completo como uma curva única (preferido) ou dois arcos.</summary>
    public static CurveArrArray Circle(string axis, double cu, double cv, double radius, bool single)
    {
        var center = AxisPoint(axis, cu, cv);
        var (xv, yv) = axis switch
        {
            "z" => (XYZ.BasisX, XYZ.BasisY),
            "x" => (XYZ.BasisY, XYZ.BasisZ),
            _ => (XYZ.BasisX, XYZ.BasisZ),
        };
        double r = Mm.ToFt(radius);
        var arr = new CurveArray();
        if (single)
            arr.Append(Arc.Create(center, r, 0, 2 * Math.PI, xv, yv));
        else
        {
            arr.Append(Arc.Create(center, r, 0, Math.PI, xv, yv));
            arr.Append(Arc.Create(center, r, Math.PI, 2 * Math.PI, xv, yv));
        }
        var loops = new CurveArrArray();
        loops.Append(arr);
        return loops;
    }

    public static Options GeometryOptions(View? view = null)
    {
        var o = new Options { ComputeReferences = true, IncludeNonVisibleObjects = false };
        if (view is null) o.DetailLevel = ViewDetailLevel.Fine;
        else o.View = view;
        return o;
    }

    public static IEnumerable<Solid> Solids(Element e)
    {
        var geom = e.get_Geometry(GeometryOptions());
        if (geom is null) yield break;
        foreach (var obj in geom)
        {
            if (obj is Solid s && s.Faces.Size > 0) yield return s;
            else if (obj is GeometryInstance gi)
                foreach (var o2 in gi.GetInstanceGeometry())
                    if (o2 is Solid s2 && s2.Faces.Size > 0) yield return s2;
        }
    }

    public static IEnumerable<PlanarFace> PlanarFaces(Element e) =>
        Solids(e).SelectMany(s => s.Faces.Cast<Face>()).OfType<PlanarFace>();

    /// <summary>
    /// Face plana com normal paralela ao eixo e no plano de coordenada dada
    /// (pés). Com <paramref name="probe"/>, exige que o ponto esteja na face.
    /// </summary>
    public static PlanarFace? FindFace(Element e, string normalAxis, double coordFt, XYZ? probe = null)
    {
        int idx = BuildContext.AxisIndex(normalAxis);
        var axisVec = BuildContext.AxisVector(normalAxis);
        PlanarFace? best = null;
        double bestDist = double.MaxValue;
        foreach (var f in PlanarFaces(e))
        {
            if (Math.Abs(Math.Abs(f.FaceNormal.DotProduct(axisVec)) - 1) > 1e-6) continue;
            if (Math.Abs(BuildContext.Component(f.Origin, idx) - coordFt) > Mm.ToFt(0.05)) continue;
            if (f.Reference is null) continue;
            if (probe is null) return f;
            var proj = f.Project(probe);
            if (proj is not null && proj.Distance < bestDist)
            {
                bestDist = proj.Distance;
                best = f;
            }
        }
        return best is not null && bestDist < Mm.ToFt(0.5) ? best : null;
    }

    /// <summary>Caixa envolvente (pés) de todos os sólidos de modelo da família.</summary>
    public static BoundingBoxXYZ? ModelBounds(Document doc, IEnumerable<Element> elements)
    {
        double[] lo = { double.MaxValue, double.MaxValue, double.MaxValue };
        double[] hi = { double.MinValue, double.MinValue, double.MinValue };
        bool any = false;
        foreach (var e in elements)
        {
            foreach (var s in Solids(e))
            {
                if (s.Volume <= 0) continue;
                var bb = s.GetBoundingBox();
                var t = bb.Transform;
                foreach (var corner in new[] { bb.Min, bb.Max, new XYZ(bb.Min.X, bb.Max.Y, bb.Min.Z), new XYZ(bb.Max.X, bb.Min.Y, bb.Max.Z) })
                {
                    var p = t.OfPoint(corner);
                    lo[0] = Math.Min(lo[0], p.X); lo[1] = Math.Min(lo[1], p.Y); lo[2] = Math.Min(lo[2], p.Z);
                    hi[0] = Math.Max(hi[0], p.X); hi[1] = Math.Max(hi[1], p.Y); hi[2] = Math.Max(hi[2], p.Z);
                    any = true;
                }
            }
        }
        if (!any) return null;
        return new BoundingBoxXYZ { Min = new XYZ(lo[0], lo[1], lo[2]), Max = new XYZ(hi[0], hi[1], hi[2]) };
    }
}
