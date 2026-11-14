using Autodesk.Revit.DB;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>
/// Teste de flexão no Revit: muda cada parâmetro editável, deixa o Revit
/// regenerar e processar as falhas, mede a geometria real e desfaz tudo.
/// </summary>
internal sealed class FlexTester
{
    private static readonly double[] Factors = { 0.85, 1.2 };

    /// <summary>Máximo de parâmetros flexionados no Revit (cada flexão regenera a
    /// família). Os de envelope vêm primeiro; o motor já conferiu todos.</summary>
    internal const int MaxParameters = 30;
    private readonly BuildContext _ctx;

    public FlexTester(BuildContext ctx) => _ctx = ctx;

    public void Run(Action<double, string> progress)
    {
        var spec = _ctx.Spec;
        var report = _ctx.Report;
        var all = spec.Parameters
            .Where(p => p.IsUserFacing && p.Kind == ParameterKinds.Length && _ctx.Params.ContainsKey(p.Id))
            .ToList();
        var editable = all.OrderBy(p => p.Role == "envelope" ? 0 : 1).ThenByDescending(p => p.NumberValue)
                          .Take(MaxParameters).ToList();
        if (all.Count > editable.Count)
            report.FlexResults.Add($"{editable.Count} de {all.Count} parâmetros flexionados no Revit (os principais); " +
                                   "o motor já tinha conferido todos.");
        var bodies = spec.Solids.Where(s => !s.IsVoid).SelectMany(s => _ctx.ElementsOf(s.Id)).ToList();
        if (editable.Count == 0 || bodies.Count == 0) return;
        var envelopeAxes = EnvelopeAxes(spec);
        int k = 0;
        foreach (var p in editable)
        {
            progress((double)k++ / editable.Count, $"Flexionando '{p.Name}'");
            var fp = _ctx.Params[p.Id];
            foreach (var factor in Factors)
            {
                double target = p.NumberValue * factor;
                if (!p.IsWithinSafeRange(target)) continue; // fora da faixa segura informada pelo motor
                envelopeAxes.TryGetValue(p.Id, out var axes);
                var r = Tx.Probe(_ctx.Doc, $"Forge 3D - flexão {p.Name}", () => _ctx.Mgr.Set(fp, Mm.ToFt(target)), () =>
                {
                    var bb = Geo.ModelBounds(_ctx.Doc, bodies);
                    if (bb is null) return "a geometria desapareceu";
                    if (axes is null) return null;
                    foreach (var axis in axes)
                    {
                        int i = BuildContext.AxisIndex(axis);
                        double size = Mm.FromFt(BuildContext.Component(bb.Max, i) - BuildContext.Component(bb.Min, i));
                        if (Math.Abs(size - target) > 1.0)
                            return $"a geometria não acompanhou no eixo {axis.ToUpperInvariant()} ({size:0.#} mm, esperado {target:0.#} mm)";
                    }
                    return null;
                });
                var label = $"{p.Name} = {target:0.#} mm";
                if (r.Ok) report.FlexResults.Add($"{label}: ok");
                else
                {
                    report.FlexOk = false;
                    report.FlexResults.Add($"{label}: FALHOU — {r.ErrorText}");
                }
            }
        }
    }

    /// <summary>Parâmetro de envelope -> eixos cujo tamanho total ele controla.</summary>
    private static Dictionary<string, List<string>> EnvelopeAxes(FamilySpec spec)
    {
        var result = new Dictionary<string, List<string>>();
        var byId = spec.Parameters.ToDictionary(p => p.Id);
        foreach (var pl in spec.Planes)
        {
            string? axis = pl.Reference switch
            {
                "left" or "right" => "x",
                "front" or "back" => "y",
                "top" => "z",
                _ => null,
            };
            if (axis is null || !byId.TryGetValue(pl.Driver, out var driver)) continue;
            var owners = new List<ParameterSpec>();
            if (driver.IsUserFacing) owners.Add(driver);
            else if (driver.HasFormula)
                owners.AddRange(Formula.ReferencedNames(driver.Formula!)
                    .Select(n => spec.Parameters.FirstOrDefault(p => p.Name == n))
                    .Where(p => p is { Role: "envelope" })!);
            foreach (var o in owners)
            {
                if (o.Role != "envelope") continue;
                if (!result.TryGetValue(o.Id, out var list)) result[o.Id] = list = new List<string>();
                if (!list.Contains(axis)) list.Add(axis);
            }
        }
        // Para o eixo Z, só a altura total controla o tamanho (a base é o nível).
        return result;
    }
}
