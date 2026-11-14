using Autodesk.Revit.DB;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>Criação de cotas lineares nas vistas certas, fora do objeto.</summary>
internal static class Dimensions
{
    /// <summary>
    /// Cota entre duas referências paralelas com normal em <paramref name="axis"/>
    /// (planos ou faces), nas posições (pés) informadas ao longo do eixo.
    /// </summary>
    public static Dimension Linear(BuildContext ctx, string axis, Reference a, double posA, Reference b, double posB)
    {
        var e = ctx.Spec.Envelope;
        double lo = Math.Min(posA, posB), hi = Math.Max(posA, posB);
        double off = ctx.NextDimensionOffsetFt(axis);
        Line line;
        View view;
        switch (axis)
        {
            case "x":
                view = ctx.Plan;
                double y = -Mm.ToFt(e.Depth / 2) - off;
                line = Line.CreateBound(new XYZ(lo, y, 0), new XYZ(hi, y, 0));
                break;
            case "y":
                view = ctx.Plan;
                double x = Mm.ToFt(e.Width / 2) + off;
                line = Line.CreateBound(new XYZ(x, lo, 0), new XYZ(x, hi, 0));
                break;
            default:
                view = ctx.Front;
                double xz = -Mm.ToFt(e.Width / 2) - off;
                line = Line.CreateBound(new XYZ(xz, 0, lo), new XYZ(xz, 0, hi));
                break;
        }
        var refs = new ReferenceArray();
        refs.Append(a);
        refs.Append(b);
        return ctx.Doc.FamilyCreate.NewLinearDimension(view, line, refs)
               ?? throw new InvalidOperationException("o Revit não criou a cota");
    }

    /// <summary>
    /// Cota de um plano até um ponto (centro de círculo) na vista indicada.
    /// <paramref name="axis"/> é o eixo da medida; <paramref name="through"/> é
    /// o ponto (pés) por onde passa a linha de cota.
    /// </summary>
    public static Dimension PlaneToPoint(BuildContext ctx, View view, string axis, Reference plane, double planePos,
        Reference point, double pointPos, XYZ through)
    {
        int idx = BuildContext.AxisIndex(axis);
        XYZ P(double v) => idx switch
        {
            0 => new XYZ(v, through.Y, through.Z),
            1 => new XYZ(through.X, v, through.Z),
            _ => new XYZ(through.X, through.Y, v),
        };
        var line = Line.CreateBound(P(Math.Min(planePos, pointPos)), P(Math.Max(planePos, pointPos)));
        var refs = new ReferenceArray();
        refs.Append(plane);
        refs.Append(point);
        return ctx.Doc.FamilyCreate.NewLinearDimension(view, line, refs)
               ?? throw new InvalidOperationException("o Revit não criou a cota");
    }
}
