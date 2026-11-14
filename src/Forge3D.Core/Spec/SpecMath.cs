namespace Forge3D.Core.Spec;

/// <summary>
/// Cálculos sobre o spec: valores finais dos parâmetros (com fórmulas),
/// posições dos planos pela árvore de cotas e âncoras. Espelha o motor.
/// </summary>
public static class SpecMath
{
    public static readonly IReadOnlyDictionary<string, string> RootAxis = new Dictionary<string, string>
    {
        ["@x0"] = "x", ["@y0"] = "y", ["@z0"] = "z",
    };

    public static bool IsRoot(string id) => RootAxis.ContainsKey(id);

    public static (string U, string V) Perpendicular(string axis) => axis switch
    {
        "z" => ("x", "y"),
        "x" => ("y", "z"),
        "y" => ("x", "z"),
        _ => throw new ArgumentException($"Eixo inválido: {axis}"),
    };

    /// <summary>Valores numéricos por id de parâmetro, com fórmulas avaliadas.</summary>
    public static Dictionary<string, double> ParameterValues(FamilySpec spec, IReadOnlyDictionary<string, double>? overrides = null)
    {
        var values = new Dictionary<string, double>();
        var formulas = new Dictionary<string, string>();
        foreach (var p in spec.Parameters)
        {
            if (!ParameterKinds.IsNumeric(p.Kind)) continue;
            if (p.HasFormula) formulas[p.Name] = p.Formula!;
            else values[p.Name] = overrides is not null && overrides.TryGetValue(p.Id, out var ov) ? ov : p.NumberValue;
        }
        var byName = Formula.EvaluateAll(values, formulas);
        var result = new Dictionary<string, double>();
        foreach (var p in spec.Parameters)
            if (byName.TryGetValue(p.Name, out var v)) result[p.Id] = v;
        return result;
    }

    public static Dictionary<string, double> OriginalPositions(FamilySpec spec)
    {
        var pos = spec.Planes.ToDictionary(p => p.Id, p => p.Position);
        foreach (var root in RootAxis.Keys) pos[root] = 0.0;
        return pos;
    }

    /// <summary>Posições dos planos para os valores dados (lado em relação ao pai = o da geometria original).</summary>
    public static Dictionary<string, double> PlanePositions(FamilySpec spec, IReadOnlyDictionary<string, double> values)
    {
        var planes = spec.Planes.ToDictionary(p => p.Id);
        var original = OriginalPositions(spec);
        var result = RootAxis.Keys.ToDictionary(k => k, _ => 0.0);
        var visiting = new HashSet<string>();

        double Pos(string id)
        {
            if (result.TryGetValue(id, out var v)) return v;
            if (!planes.TryGetValue(id, out var p)) throw new InvalidOperationException($"Plano inexistente: {id}");
            if (!visiting.Add(id)) throw new InvalidOperationException($"Ciclo na árvore de planos envolvendo '{id}'.");
            var parentPos = Pos(p.Parent);
            var sign = original[id] >= original[p.Parent] ? 1.0 : -1.0;
            var d = values.TryGetValue(p.Driver, out var dv) ? dv : throw new InvalidOperationException($"Parâmetro inexistente: {p.Driver}");
            result[id] = parentPos + sign * d;
            visiting.Remove(id);
            return result[id];
        }

        foreach (var id in planes.Keys) Pos(id);
        return result;
    }

    public static double AnchorPosition(AnchorSpec a, IReadOnlyDictionary<string, double> planes,
        IReadOnlyDictionary<string, double> original, IReadOnlyDictionary<string, double> values)
    {
        if (a.Driver is null) return a.Position;
        var sign = a.Position >= original[a.Parent] ? 1.0 : -1.0;
        return planes[a.Parent] + sign * values[a.Driver];
    }
}
