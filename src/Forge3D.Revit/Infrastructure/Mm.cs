using Autodesk.Revit.DB;

namespace Forge3D.Revit.Infrastructure;

/// <summary>Conversão entre milímetros (spec) e pés (unidade interna do Revit).</summary>
internal static class Mm
{
    public const double PerFoot = 304.8;

    public static double ToFt(double mm) => mm / PerFoot;

    public static double FromFt(double ft) => ft * PerFoot;

    public static XYZ Xyz(double xMm, double yMm, double zMm) => new(ToFt(xMm), ToFt(yMm), ToFt(zMm));
}
