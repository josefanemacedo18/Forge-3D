namespace Forge3D.Core.Spec;

/// <summary>
/// Validação semântica do FamilySpec antes de qualquer operação no Revit.
/// Espelha as regras do motor: árvore de planos sem ciclos, cotas coerentes,
/// fórmulas resolvíveis, referências existentes, nomes únicos e segmentos
/// com o comprimento mínimo aceito pelo Revit.
/// </summary>
public static class SpecValidator
{
    public const double PositionTolerance = 0.1; // mm
    public const double MinSegment = 1.0; // mm — o Revit rejeita curvas menores que ~0,78 mm
    public const double MinPlaneGap = 1.0; // mm

    private static readonly HashSet<string> Categories = new()
    {
        "GenericModel", "Furniture", "FurnitureSystems", "Casework", "LightingFixtures", "Planting", "Entourage",
        "SpecialtyEquipment", "PlumbingFixtures", "Site", "Parking", "MechanicalEquipment", "ElectricalEquipment",
        "ElectricalFixtures", "FoodServiceEquipment", "MedicalEquipment", "Signage",
    };

    public static List<string> Validate(FamilySpec spec)
    {
        var errors = new List<string>();
        if (spec.Schema != FamilySpec.SchemaName) errors.Add($"Formato desconhecido: '{spec.Schema}'.");
        if (spec.Version != FamilySpec.SupportedVersion)
            errors.Add($"Versão {spec.Version} do FamilySpec não suportada por este plugin (esperada {FamilySpec.SupportedVersion}).");
        if (spec.Units != "mm") errors.Add("O FamilySpec precisa estar em milímetros.");
        if (string.IsNullOrWhiteSpace(spec.Family.Name)) errors.Add("A família precisa de um nome.");
        if (!Categories.Contains(spec.Family.Category)) errors.Add($"Categoria desconhecida: '{spec.Family.Category}'.");
        if (spec.Envelope.Width <= 0 || spec.Envelope.Depth <= 0 || spec.Envelope.Height <= 0)
            errors.Add("O envelope precisa ter largura, profundidade e altura positivas.");
        if (errors.Count > 0) return errors;

        // Parâmetros.
        var byId = new Dictionary<string, ParameterSpec>();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var p in spec.Parameters)
        {
            if (!byId.TryAdd(p.Id, p)) errors.Add($"Parâmetro com id repetido: '{p.Id}'.");
            if (!names.Add(p.Name)) errors.Add($"Nome de parâmetro repetido: '{p.Name}'.");
            if (!Formula.IsValidName(p.Name)) errors.Add($"Nome de parâmetro inválido: '{p.Name}'.");
            if (p.HasFormula && !ParameterKinds.IsNumeric(p.Kind))
                errors.Add($"Parâmetro '{p.Name}' do tipo {p.Kind} não pode ter fórmula numérica.");
        }
        var materialIds = spec.Materials.Select(m => m.Id).ToHashSet();
        foreach (var p in spec.Parameters)
        {
            if (p.HasFormula)
            {
                try
                {
                    foreach (var n in Formula.ReferencedNames(p.Formula!))
                        if (!names.Contains(n)) errors.Add($"Fórmula de '{p.Name}' cita parâmetro inexistente '{n}'.");
                }
                catch (FormulaException ex) { errors.Add(ex.Message); }
            }
            else if (p.Kind == ParameterKinds.Length && p.NumberValue <= 0)
                errors.Add($"Parâmetro de comprimento '{p.Name}' precisa de valor positivo.");
            if (p.Kind == ParameterKinds.Material && p.StringValue is { } mid && !materialIds.Contains(mid))
                errors.Add($"Parâmetro de material '{p.Name}' aponta para material inexistente '{mid}'.");
        }
        if (errors.Count > 0) return errors;

        Dictionary<string, double> values;
        try { values = SpecMath.ParameterValues(spec); }
        catch (FormulaException ex) { return new List<string> { ex.Message }; }

        foreach (var p in spec.Parameters.Where(p => p.Kind == ParameterKinds.Length && p.HasFormula))
        {
            var v = values[p.Id];
            if (!(v > 0)) errors.Add($"Fórmula de '{p.Name}' resulta em {v:0.###} mm; comprimentos rotulados precisam ser positivos.");
        }

        // Planos.
        var axisOf = new Dictionary<string, string>(SpecMath.RootAxis);
        var planeNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pl in spec.Planes)
        {
            if (SpecMath.IsRoot(pl.Id) || !axisOf.TryAdd(pl.Id, pl.Axis)) errors.Add($"Plano com id repetido ou reservado: '{pl.Id}'.");
            if (pl.Axis is not ("x" or "y" or "z")) errors.Add($"Plano '{pl.Id}' com eixo inválido '{pl.Axis}'.");
            if (!planeNames.Add(pl.Name)) errors.Add($"Nome de plano repetido: '{pl.Name}'.");
        }
        var original = SpecMath.OriginalPositions(spec);
        foreach (var pl in spec.Planes)
        {
            if (!axisOf.TryGetValue(pl.Parent, out var parentAxis)) { errors.Add($"Plano '{pl.Id}' tem pai inexistente '{pl.Parent}'."); continue; }
            if (parentAxis != pl.Axis) errors.Add($"Plano '{pl.Id}' (eixo {pl.Axis}) tem pai de outro eixo.");
            if (!byId.TryGetValue(pl.Driver, out var drv)) { errors.Add($"Plano '{pl.Id}' é rotulado por parâmetro inexistente '{pl.Driver}'."); continue; }
            if (drv.Kind != ParameterKinds.Length) { errors.Add($"Plano '{pl.Id}' é rotulado por parâmetro que não é comprimento."); continue; }
            var dist = Math.Abs(pl.Position - original[pl.Parent]);
            if (dist < MinPlaneGap) errors.Add($"Plano '{pl.Id}' está colado ao pai (distância {dist:0.###} mm).");
            if (Math.Abs(dist - values[drv.Id]) > PositionTolerance)
                errors.Add($"Plano '{pl.Id}': distância ao pai ({dist:0.###} mm) difere de '{drv.Name}' ({values[drv.Id]:0.###} mm).");
        }
        if (errors.Count > 0) return errors;
        try
        {
            var positions = SpecMath.PlanePositions(spec, values);
            foreach (var pl in spec.Planes)
                if (Math.Abs(positions[pl.Id] - pl.Position) > PositionTolerance)
                    errors.Add($"Plano '{pl.Id}' não reproduz sua posição pela árvore de cotas.");
        }
        catch (InvalidOperationException ex) { errors.Add(ex.Message); return errors; }

        // Sólidos.
        var solidIds = new HashSet<string>();
        foreach (var s in spec.Solids)
        {
            if (!solidIds.Add(s.Id)) errors.Add($"Sólido com id repetido: '{s.Id}'.");
            if (s.Material is not null && (!byId.TryGetValue(s.Material, out var mp) || mp.Kind != ParameterKinds.Material))
                errors.Add($"Sólido '{s.Id}' usa parâmetro de material inexistente '{s.Material}'.");
            switch (s.Kind)
            {
                case SolidKinds.Box:
                    foreach (var (ax, refs) in new[] { ("x", s.X), ("y", s.Y), ("z", s.Z) })
                    {
                        if (refs is not { Length: 2 }) { errors.Add($"Caixa '{s.Id}' sem as duas faces {ax}."); continue; }
                        if (refs.Any(r => !axisOf.TryGetValue(r, out var a) || a != ax))
                            errors.Add($"Caixa '{s.Id}': face {ax} referencia plano inexistente ou de outro eixo.");
                        else if (!(original[refs[0]] < original[refs[1]] - 0.05))
                            errors.Add($"Caixa '{s.Id}': planos {ax} fora de ordem ou coincidentes.");
                    }
                    break;
                case SolidKinds.Cylinder:
                case SolidKinds.Revolution:
                    ValidateAxial(s, axisOf, byId, original, values, errors);
                    break;
                case SolidKinds.Extrusion:
                    ValidateExtrusion(s, axisOf, original, errors);
                    break;
                case SolidKinds.Mesh:
                    if (s.Vertices is null || s.Faces is null || s.Vertices.Count < 4 || s.Faces.Count < 4)
                        errors.Add($"Malha '{s.Id}' tem geometria insuficiente.");
                    else if (s.Faces.Any(f => f.Length != 3 || f.Any(i => i < 0 || i >= s.Vertices.Count)))
                        errors.Add($"Malha '{s.Id}' referencia vértice inexistente.");
                    break;
                default:
                    errors.Add($"Sólido '{s.Id}' de tipo desconhecido '{s.Kind}'.");
                    break;
            }
        }

        // Tipos.
        if (spec.Types.Count == 0) errors.Add("O spec precisa de ao menos um tipo de família.");
        var typeNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var t in spec.Types)
        {
            if (!typeNames.Add(t.Name)) errors.Add($"Tipo de família repetido: '{t.Name}'.");
            foreach (var key in t.Values.Keys)
            {
                if (!byId.TryGetValue(key, out var p)) errors.Add($"Tipo '{t.Name}' define parâmetro inexistente '{key}'.");
                else if (p.HasFormula) errors.Add($"Tipo '{t.Name}' define '{p.Name}', que é calculado por fórmula.");
            }
        }
        return errors;
    }

    private static void ValidateAxial(SolidSpec s, Dictionary<string, string> axisOf, Dictionary<string, ParameterSpec> byId,
        Dictionary<string, double> original, Dictionary<string, double> values, List<string> errors)
    {
        if (s.Axis is not ("x" or "y" or "z") || s.Center is null) { errors.Add($"{s.Kind} '{s.Id}' sem eixo ou centro."); return; }
        var (u, v) = SpecMath.Perpendicular(s.Axis);
        foreach (var (key, axis, anchor) in new[] { ("u", u, s.Center.U), ("v", v, s.Center.V) })
        {
            if (!axisOf.TryGetValue(anchor.Parent, out var a) || a != axis)
            {
                errors.Add($"{s.Kind} '{s.Id}': âncora {key} usa plano inexistente ou de outro eixo.");
                continue;
            }
            if (anchor.Driver is null) continue;
            if (!byId.TryGetValue(anchor.Driver, out var d) || d.Kind != ParameterKinds.Length)
                errors.Add($"{s.Kind} '{s.Id}': âncora {key} rotulada por parâmetro inválido.");
            else if (Math.Abs(Math.Abs(anchor.Position - original[anchor.Parent]) - values[d.Id]) > PositionTolerance)
                errors.Add($"{s.Kind} '{s.Id}': âncora {key} incoerente com '{d.Name}'.");
        }
        if (s.Extent is not null)
        {
            if (s.Extent.Length != 2 || s.Extent.Any(r => !axisOf.TryGetValue(r, out var a) || a != s.Axis))
                errors.Add($"{s.Kind} '{s.Id}': extensão com plano inválido.");
            else if (!(original[s.Extent[0]] < original[s.Extent[1]] - 0.05))
                errors.Add($"{s.Kind} '{s.Id}': planos de extensão fora de ordem.");
        }
        if (s.Kind == SolidKinds.Cylinder)
        {
            if (s.Extent is null) errors.Add($"Cilindro '{s.Id}' sem extensão.");
            if (s.Diameter is null || s.Diameter.Value <= 0) errors.Add($"Cilindro '{s.Id}' sem diâmetro válido.");
            else if (s.Diameter.Driver is not null)
            {
                if (!byId.TryGetValue(s.Diameter.Driver, out var d) || d.Kind != ParameterKinds.Length)
                    errors.Add($"Cilindro '{s.Id}': diâmetro rotulado por parâmetro inválido.");
                else if (Math.Abs(values[d.Id] - s.Diameter.Value) > PositionTolerance)
                    errors.Add($"Cilindro '{s.Id}': diâmetro difere do parâmetro '{d.Name}'.");
            }
        }
        else
        {
            if (s.Profile is null || s.Profile.Count < 2 || s.Range is not { Length: 2 }) { errors.Add($"Revolução '{s.Id}' sem perfil."); return; }
            if (s.Profile.Any(seg => seg.Start[0] < -0.01 || seg.End[0] < -0.01))
                errors.Add($"Revolução '{s.Id}': perfil cruza o eixo (raio negativo).");
            ValidateLoop(s.Profile, $"Revolução '{s.Id}'", errors);
            var (uAxis, _) = SpecMath.Perpendicular(s.Axis);
            foreach (var al in s.Alignments ?? new())
            {
                if (al.Loop != 0 || al.Segment < 0 || al.Segment >= s.Profile.Count)
                {
                    errors.Add($"Revolução '{s.Id}': alinhamento aponta para segmento inexistente.");
                    continue;
                }
                var seg = s.Profile[al.Segment];
                if (seg.IsArc || Math.Abs(seg.Start[0] - seg.End[0]) > 0.05)
                {
                    errors.Add($"Revolução '{s.Id}': só trechos verticais do perfil podem ser travados.");
                    continue;
                }
                if (!axisOf.TryGetValue(al.Plane, out var pax) || pax != uAxis)
                {
                    errors.Add($"Revolução '{s.Id}': plano radial '{al.Plane}' inválido.");
                    continue;
                }
                if (Math.Abs(s.Center.U.Position + seg.Start[0] - original[al.Plane]) > 0.05)
                    errors.Add($"Revolução '{s.Id}': trecho radial não está sobre o plano '{al.Plane}'.");
            }
        }
    }

    private static void ValidateExtrusion(SolidSpec s, Dictionary<string, string> axisOf, Dictionary<string, double> original, List<string> errors)
    {
        if (s.Loops is null || s.Loops.Count == 0) { errors.Add($"Extrusão '{s.Id}' sem perfil."); return; }
        for (int i = 0; i < s.Loops.Count; i++) ValidateLoop(s.Loops[i], $"Extrusão '{s.Id}' laço {i}", errors);
        if (s.Frame is not null)
        {
            if (s.Depth is not > 0) errors.Add($"Extrusão '{s.Id}' com profundidade inválida.");
            var x = s.Frame.XDir; var y = s.Frame.YDir;
            double dot = x[0] * y[0] + x[1] * y[1] + x[2] * y[2];
            if (Math.Abs(dot) > 1e-4) errors.Add($"Extrusão '{s.Id}': eixos do plano não são perpendiculares.");
            return;
        }
        if (s.Axis is not ("x" or "y" or "z") || s.Extent is not { Length: 2 })
        {
            errors.Add($"Extrusão '{s.Id}' precisa de eixo e extensão (ou de um plano com profundidade).");
            return;
        }
        if (s.Extent.Any(r => !axisOf.TryGetValue(r, out var a) || a != s.Axis))
            errors.Add($"Extrusão '{s.Id}': extensão com plano inválido.");
        else if (!(original[s.Extent[0]] < original[s.Extent[1]] - 0.05))
            errors.Add($"Extrusão '{s.Id}': planos de extensão fora de ordem.");
        var (u, v) = SpecMath.Perpendicular(s.Axis);
        foreach (var al in s.Alignments ?? new())
        {
            if (al.Loop < 0 || al.Loop >= s.Loops.Count || al.Segment < 0 || al.Segment >= s.Loops[al.Loop].Count)
            {
                errors.Add($"Extrusão '{s.Id}': alinhamento aponta para segmento inexistente.");
                continue;
            }
            var seg = s.Loops[al.Loop][al.Segment];
            if (seg.IsArc) { errors.Add($"Extrusão '{s.Id}': só segmentos retos podem ser alinhados."); continue; }
            if (!axisOf.TryGetValue(al.Plane, out var pax) || (pax != u && pax != v))
            {
                errors.Add($"Extrusão '{s.Id}': alinhamento com plano de eixo inválido.");
                continue;
            }
            int idx = pax == u ? 0 : 1;
            var c = original[al.Plane];
            if (Math.Abs(seg.Start[idx] - c) > 0.05 || Math.Abs(seg.End[idx] - c) > 0.05)
                errors.Add($"Extrusão '{s.Id}': segmento alinhado não está sobre o plano '{al.Plane}'.");
        }
    }

    private static void ValidateLoop(List<SegmentSpec> loop, string label, List<string> errors)
    {
        int n = loop.Count;
        if (n < 2) { errors.Add($"{label}: laço com menos de dois segmentos."); return; }
        for (int i = 0; i < n; i++)
        {
            var seg = loop[i];
            var next = loop[(i + 1) % n];
            if (seg.Start.Length != 2 || seg.End.Length != 2 || (seg.IsArc && seg.Mid is not { Length: 2 }))
            {
                errors.Add($"{label}: segmento {i} mal formado.");
                continue;
            }
            if (Dist(seg.End, next.Start) > 0.05) errors.Add($"{label}: segmentos {i} e {(i + 1) % n} não se conectam.");
            if (Dist(seg.Start, seg.End) < MinSegment) errors.Add($"{label}: segmento {i} menor que {MinSegment} mm.");
        }
    }

    private static double Dist(double[] a, double[] b) => Math.Sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]));
}
