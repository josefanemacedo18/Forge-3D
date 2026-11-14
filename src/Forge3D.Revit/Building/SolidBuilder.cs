using Autodesk.Revit.DB;
using Forge3D.Core.Spec;
using Forge3D.Revit.Infrastructure;

namespace Forge3D.Revit.Building;

/// <summary>
/// Cria os sólidos do spec e trava suas faces nos planos de referência.
/// Cada sólido e cada restrição rodam em transação própria.
/// </summary>
internal sealed class SolidBuilder
{
    private const string Stage = "Geometria";
    private const string Constraints = "Restrições";
    private readonly BuildContext _ctx;

    public SolidBuilder(BuildContext ctx) => _ctx = ctx;

    private Document Doc => _ctx.Doc;

    public void Build(SolidSpec s)
    {
        try
        {
            switch (s.Kind)
            {
                case SolidKinds.Box: BuildBox(s); break;
                case SolidKinds.Cylinder: BuildCylinder(s); break;
                case SolidKinds.Extrusion when s.Frame is not null: BuildFramedExtrusion(s); break;
                case SolidKinds.Extrusion: BuildAxisExtrusion(s); break;
                case SolidKinds.Revolution: BuildRevolution(s); break;
                case SolidKinds.Mesh: BuildMesh(s); break;
                default: _ctx.Report.Fail(Stage, s.Name, $"tipo desconhecido '{s.Kind}'"); break;
            }
        }
        catch (Exception ex)
        {
            _ctx.Report.Fail(Stage, s.Name, ex.Message);
            Log.Error($"Falha ao criar o sólido '{s.Name}'", ex);
        }
    }

    // -- criação --------------------------------------------------------------------------

    private bool Create(SolidSpec s, Func<Element> factory)
    {
        Element? created = null;
        var r = Tx.Run(Doc, $"Forge 3D - {s.Name}", () =>
        {
            created = factory();
            ApplyVisibility(created, s.Visibility);
        });
        if (!r.Ok || created is null)
        {
            _ctx.Report.Fail(Stage, s.Name, r.ErrorText);
            return false;
        }
        _ctx.Solids[s.Id] = created;
        _ctx.Report.Ok(Stage, s.Name, s.Kind);
        _ctx.Report.Warnings.AddRange(r.Warnings);
        return true;
    }

    private SketchPlane NewSketchPlane(Plane plane) => SketchPlane.Create(Doc, plane);

    private Extrusion NewExtrusion(bool solid, CurveArrArray profile, Plane plane, double h0Ft, double h1Ft)
    {
        var sp = NewSketchPlane(plane);
        var ext = Doc.FamilyCreate.NewExtrusion(solid, profile, sp, h1Ft - h0Ft)
                  ?? throw new InvalidOperationException("o Revit não criou a extrusão");
        // Desloca para [h0, h1] a partir do plano de esboço (que passa pela origem).
        ext.EndOffset = h1Ft;
        ext.StartOffset = h0Ft;
        return ext;
    }

    private void BuildBox(SolidSpec s)
    {
        double x0 = _ctx.PositionFt(s.X![0]), x1 = _ctx.PositionFt(s.X[1]);
        double y0 = _ctx.PositionFt(s.Y![0]), y1 = _ctx.PositionFt(s.Y[1]);
        double z0 = _ctx.PositionFt(s.Z![0]), z1 = _ctx.PositionFt(s.Z[1]);
        if (!Create(s, () =>
            {
                var loop = new CurveArray();
                loop.Append(Line.CreateBound(new XYZ(x0, y0, 0), new XYZ(x1, y0, 0)));
                loop.Append(Line.CreateBound(new XYZ(x1, y0, 0), new XYZ(x1, y1, 0)));
                loop.Append(Line.CreateBound(new XYZ(x1, y1, 0), new XYZ(x0, y1, 0)));
                loop.Append(Line.CreateBound(new XYZ(x0, y1, 0), new XYZ(x0, y0, 0)));
                var arr = new CurveArrArray();
                arr.Append(loop);
                return NewExtrusion(!s.IsVoid, arr, Geo.AxisPlane("z"), z0, z1);
            })) return;
        var e = _ctx.Solids[s.Id];
        var mid = new XYZ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
        Align(s, e, "x", s.X[0], x0, new XYZ(x0, mid.Y, mid.Z));
        Align(s, e, "x", s.X[1], x1, new XYZ(x1, mid.Y, mid.Z));
        Align(s, e, "y", s.Y[0], y0, new XYZ(mid.X, y0, mid.Z));
        Align(s, e, "y", s.Y[1], y1, new XYZ(mid.X, y1, mid.Z));
        Align(s, e, "z", s.Z[0], z0, new XYZ(mid.X, mid.Y, z0));
        Align(s, e, "z", s.Z[1], z1, new XYZ(mid.X, mid.Y, z1));
    }

    private void BuildCylinder(SolidSpec s)
    {
        var axis = s.Axis!;
        double cu = s.Center!.U.Position, cv = s.Center.V.Position;
        double h0 = _ctx.PositionFt(s.Extent![0]), h1 = _ctx.PositionFt(s.Extent[1]);
        double radius = s.Diameter!.Value / 2;
        bool single = true;
        bool ok = Create(s, () => NewExtrusion(!s.IsVoid, Geo.Circle(axis, cu, cv, radius, true), Geo.AxisPlane(axis), h0, h1));
        if (!ok)
        {
            // Alguns contextos não aceitam círculo de curva única: tenta dois arcos.
            _ctx.Report.Items.RemoveAt(_ctx.Report.Items.Count - 1);
            single = false;
            if (!Create(s, () => NewExtrusion(!s.IsVoid, Geo.Circle(axis, cu, cv, radius, false), Geo.AxisPlane(axis), h0, h1)))
                return;
        }
        var e = (Extrusion)_ctx.Solids[s.Id];
        var c0 = Geo.AxisPoint(axis, cu, cv, Mm.FromFt(h0));
        var c1 = Geo.AxisPoint(axis, cu, cv, Mm.FromFt(h1));
        Align(s, e, axis, s.Extent[0], h0, c0);
        Align(s, e, axis, s.Extent[1], h1, c1);
        if (s.Diameter.Driver is not null) LabelDiameter(s, e, axis, cu, cv, radius, h1, single);
        foreach (var (key, anchor) in new[] { ("u", s.Center.U), ("v", s.Center.V) })
            if (anchor.Driver is not null) DimensionCenter(s, e, axis, key, anchor, cu, cv, h1);
    }

    private void BuildAxisExtrusion(SolidSpec s)
    {
        var axis = s.Axis!;
        double h0 = _ctx.PositionFt(s.Extent![0]), h1 = _ctx.PositionFt(s.Extent[1]);
        XYZ Map(double u, double v) => Geo.AxisPoint(axis, u, v);
        if (!Create(s, () => NewExtrusion(!s.IsVoid, Geo.Loops(s.Loops!, Map), Geo.AxisPlane(axis), h0, h1))) return;
        var e = _ctx.Solids[s.Id];
        var (uAxis, _) = SpecMath.Perpendicular(axis);
        var first = s.Loops![0][0];
        Align(s, e, axis, s.Extent[0], h0, Geo.AxisPoint(axis, first.Start[0], first.Start[1], Mm.FromFt(h0)), allowEdgeProbe: true);
        Align(s, e, axis, s.Extent[1], h1, Geo.AxisPoint(axis, first.Start[0], first.Start[1], Mm.FromFt(h1)), allowEdgeProbe: true);
        double hm = Mm.FromFt((h0 + h1) / 2);
        foreach (var al in s.Alignments ?? new())
        {
            var seg = s.Loops[al.Loop][al.Segment];
            var planeAxis = _ctx.AxisOf(al.Plane);
            double mu = (seg.Start[0] + seg.End[0]) / 2, mv = (seg.Start[1] + seg.End[1]) / 2;
            double coord = planeAxis == uAxis ? mu : mv;
            Align(s, e, planeAxis, al.Plane, Mm.ToFt(coord), Geo.AxisPoint(axis, mu, mv, hm));
        }
    }

    private void BuildFramedExtrusion(SolidSpec s)
    {
        var f = s.Frame!;
        var origin = Mm.Xyz(f.Origin[0], f.Origin[1], f.Origin[2]);
        var xd = new XYZ(f.XDir[0], f.XDir[1], f.XDir[2]).Normalize();
        var yd = new XYZ(f.YDir[0], f.YDir[1], f.YDir[2]).Normalize();
        XYZ Map(double u, double v) => origin + xd * Mm.ToFt(u) + yd * Mm.ToFt(v);
        Create(s, () =>
        {
            var sp = NewSketchPlane(Plane.CreateByOriginAndBasis(origin, xd, yd));
            return Doc.FamilyCreate.NewExtrusion(!s.IsVoid, Geo.Loops(s.Loops!, Map), sp, Mm.ToFt(s.Depth!.Value))
                   ?? throw new InvalidOperationException("o Revit não criou a extrusão");
        });
    }

    private void BuildRevolution(SolidSpec s)
    {
        var axis = s.Axis!;
        double cu = s.Center!.U.Position, cv = s.Center.V.Position;
        double h0 = s.Range![0], h1 = s.Range[1];
        XYZ Map(double r, double h) => axis switch
        {
            "z" => Mm.Xyz(cu + r, cv, h),
            "x" => Mm.Xyz(h, cu + r, cv),
            _ => Mm.Xyz(cu + r, h, cv),
        };
        Plane plane = axis == "z"
            ? Plane.CreateByNormalAndOrigin(XYZ.BasisY, Mm.Xyz(0, cv, 0))
            : Plane.CreateByNormalAndOrigin(XYZ.BasisZ, Mm.Xyz(0, 0, cv));
        var axisLine = Line.CreateBound(Map(0, h0), Map(0, h1));
        if (!Create(s, () =>
            {
                var sp = NewSketchPlane(plane);
                var arr = new CurveArrArray();
                arr.Append(Geo.Loop(s.Profile!, Map));
                return Doc.FamilyCreate.NewRevolution(!s.IsVoid, arr, sp, axisLine, 0, 2 * Math.PI)
                       ?? throw new InvalidOperationException("o Revit não criou a revolução");
            })) return;
        if (s.Extent is null) return;
        var e = _ctx.Solids[s.Id];
        foreach (var (planeId, h) in new[] { (s.Extent[0], h0), (s.Extent[1], h1) })
        {
            var cap = s.Profile!.FirstOrDefault(seg => !seg.IsArc && Math.Abs(seg.Start[1] - h) < 0.05 && Math.Abs(seg.End[1] - h) < 0.05);
            double rProbe = cap is null ? 0 : (cap.Start[0] + cap.End[0]) / 2;
            Align(s, e, axis, planeId, Mm.ToFt(h), Map(rProbe, h));
        }
        if (s.Center.U.Driver is not null || s.Center.V.Driver is not null)
            _ctx.Report.Fail(Constraints, s.Name, "posição da revolução fora do centro fica fixa nesta versão");
    }

    private void BuildMesh(SolidSpec s)
    {
        var verts = s.Vertices!.Select(v => Mm.Xyz(v[0], v[1], v[2])).ToList();
        var materialId = s.Material is not null && _ctx.Spec.FindParameter(s.Material)?.StringValue is { } mid
                         && _ctx.Materials.TryGetValue(mid, out var m) ? m : ElementId.InvalidElementId;

        TessellatedShapeBuilderResult BuildShape(TessellatedShapeBuilderTarget target, TessellatedShapeBuilderFallback fallback)
        {
            var b = new TessellatedShapeBuilder { Target = target, Fallback = fallback, GraphicsStyleId = ElementId.InvalidElementId };
            b.OpenConnectedFaceSet(true);
            foreach (var f in s.Faces!)
            {
                var face = new TessellatedFace(new List<XYZ> { verts[f[0]], verts[f[1]], verts[f[2]] }, materialId);
                if (b.DoesFaceHaveEnoughLoopsAndVertices(face)) b.AddFace(face);
            }
            b.CloseConnectedFaceSet();
            b.Build();
            return b.GetBuildResult();
        }

        // 1) Sólido de forma livre (aceita material e visibilidade da família).
        bool asSolid = Create(s, () =>
        {
            var res = BuildShape(TessellatedShapeBuilderTarget.Solid, TessellatedShapeBuilderFallback.Abort);
            var solid = res.GetGeometricalObjects().OfType<Solid>().FirstOrDefault(x => x.Volume > 0)
                        ?? throw new InvalidOperationException("malha não forma um sólido fechado");
            return FreeFormElement.Create(Doc, solid);
        });
        if (asSolid) return;
        _ctx.Report.Items.RemoveAt(_ctx.Report.Items.Count - 1);
        // 2) Forma direta com malha (geometria fiel, sem material paramétrico).
        Create(s, () =>
        {
            var res = BuildShape(TessellatedShapeBuilderTarget.AnyGeometry, TessellatedShapeBuilderFallback.Mesh);
            var catId = new ElementId(BuiltInCategory.OST_GenericModel);
            var ownerCat = Doc.OwnerFamily.FamilyCategory?.Id;
            if (ownerCat is not null && DirectShape.IsValidCategoryId(ownerCat, Doc)) catId = ownerCat;
            var ds = DirectShape.CreateElement(Doc, catId);
            ds.SetShape(res.GetGeometricalObjects());
            return ds;
        });
        _ctx.Report.Warnings.Add($"'{s.Name}' ficou como forma direta (malha aberta ou não manifold); material fixo.");
    }

    // -- restrições ---------------------------------------------------------------------------

    /// <summary>Trava a face plana (normal no eixo, na coordenada dada) ao plano do spec.</summary>
    private void Align(SolidSpec s, Element e, string normalAxis, string planeId, double coordFt, XYZ probe, bool allowEdgeProbe = false)
    {
        var face = Geo.FindFace(e, normalAxis, coordFt, probe) ?? (allowEdgeProbe ? Geo.FindFace(e, normalAxis, coordFt) : null);
        if (face is null)
        {
            _ctx.Report.Fail(Constraints, $"{s.Name} ↔ {PlaneLabel(planeId)}", "face correspondente não encontrada");
            return;
        }
        var view = _ctx.ViewForNormalAxis(normalAxis);
        var r = Tx.Run(Doc, $"Forge 3D - travar {s.Name}", () =>
            Doc.FamilyCreate.NewAlignment(view, _ctx.PlaneRef(planeId), face.Reference));
        if (r.Ok) _ctx.Report.Ok(Constraints, $"{s.Name} ↔ {PlaneLabel(planeId)}");
        else _ctx.Report.Fail(Constraints, $"{s.Name} ↔ {PlaneLabel(planeId)}", r.ErrorText);
    }

    private string PlaneLabel(string id) => id switch
    {
        "@x0" => "Centro (Esq/Dir)",
        "@y0" => "Centro (Frente/Fundo)",
        "@z0" => "Nível de referência",
        _ => _ctx.Spec.FindPlane(id)?.Name ?? id,
    };

    private IEnumerable<Curve> ProfileCurves(Extrusion e)
    {
        foreach (CurveArray loop in e.Sketch.Profile)
            foreach (Curve c in loop)
                yield return c;
    }

    private void LabelDiameter(SolidSpec s, Extrusion e, string axis, double cu, double cv, double radius, double h1Ft, bool singleCircle)
    {
        var fp = _ctx.Params[s.Diameter!.Driver!];
        var view = _ctx.ViewLookingAlong(axis);
        var (uAxis, vAxis) = SpecMath.Perpendicular(axis);
        var outside = Geo.AxisPoint(axis, cu + radius * 1.4, cv + radius * 0.6, Mm.FromFt(h1Ft));
        var attempts = new List<(string Name, Func<Reference?> GetRef)>();
        if (singleCircle)
            attempts.Add(("esboço", () => ProfileCurves(e).FirstOrDefault()?.Reference));
        attempts.Add(("aresta", () => CircularEdgeRef(e, axis, h1Ft, radius)));
        foreach (var (name, getRef) in attempts)
        {
            var r = Tx.Run(Doc, $"Forge 3D - diâmetro {s.Name}", () =>
            {
                var reference = getRef() ?? throw new InvalidOperationException("referência do círculo não encontrada");
                var dim = Doc.FamilyCreate.NewDiameterDimension(view, reference, outside)
                          ?? throw new InvalidOperationException("o Revit não criou a cota de diâmetro");
                dim.FamilyLabel = fp;
            });
            if (r.Ok)
            {
                _ctx.Report.Ok(Constraints, $"{s.Name}: diâmetro", $"{fp.Definition.Name} ({name})");
                return;
            }
            Log.Info($"Diâmetro de '{s.Name}' pela estratégia '{name}' falhou: {r.ErrorText}");
        }
        _ctx.Report.Fail(Constraints, $"{s.Name}: diâmetro", "o Revit não aceitou a cota de diâmetro rotulada; diâmetro fixo");
    }

    private Reference? CircularEdgeRef(Element e, string axis, double hFt, double radiusMm)
    {
        int idx = BuildContext.AxisIndex(axis);
        foreach (var solid in Geo.Solids(e))
            foreach (Edge edge in solid.Edges)
            {
                if (edge.AsCurve() is Arc arc && Math.Abs(Mm.FromFt(arc.Radius) - radiusMm) < 0.1 &&
                    Math.Abs(BuildContext.Component(arc.Center, idx) - hFt) < Mm.ToFt(0.1))
                    return edge.Reference;
            }
        return null;
    }

    private void DimensionCenter(SolidSpec s, Extrusion e, string axis, string key, AnchorSpec anchor, double cu, double cv, double h1Ft)
    {
        var fp = _ctx.Params[anchor.Driver!];
        var (uAxis, vAxis) = SpecMath.Perpendicular(axis);
        var measureAxis = key == "u" ? uAxis : vAxis;
        var view = _ctx.ViewLookingAlong(axis);
        var center3 = Geo.AxisPoint(axis, cu, cv, Mm.FromFt(h1Ft));
        double pointPos = Mm.ToFt(key == "u" ? cu : cv);
        var r = Tx.Run(Doc, $"Forge 3D - centro {s.Name}", () =>
        {
            var curve = ProfileCurves(e).FirstOrDefault() ?? throw new InvalidOperationException("perfil sem curvas");
            var element = Doc.GetElement(curve.Reference) as CurveElement
                          ?? throw new InvalidOperationException("curva do esboço não encontrada");
            // Como no Revit: a marca de centro precisa estar visível para cotar até o centro.
            var mark = element.get_Parameter(BuiltInParameter.ARC_CURVE_CNTR_MRK_VISIBLE);
            if (mark is { IsReadOnly: false } && mark.AsInteger() != 1)
            {
                mark.Set(1);
                Doc.Regenerate();
            }
            var centerRef = element.CenterPointReference ?? throw new InvalidOperationException("centro do círculo sem referência");
            var dim = Dimensions.PlaneToPoint(_ctx, view, measureAxis, _ctx.PlaneRef(anchor.Parent), _ctx.PositionFt(anchor.Parent),
                centerRef, pointPos, center3);
            dim.FamilyLabel = fp;
        });
        if (r.Ok) _ctx.Report.Ok(Constraints, $"{s.Name}: centro {measureAxis.ToUpperInvariant()}", fp.Definition.Name);
        else _ctx.Report.Fail(Constraints, $"{s.Name}: centro {measureAxis.ToUpperInvariant()}", r.ErrorText);
    }

    // -- materiais, visibilidade e vazios ------------------------------------------------------

    private static void ApplyVisibility(Element e, string visibility)
    {
        if (visibility == "all" || e is not GenericForm form) return;
        var vis = new FamilyElementVisibility(FamilyElementVisibilityType.Model)
        {
            IsShownInCoarse = false,
            IsShownInMedium = visibility != "fine",
            IsShownInFine = true,
        };
        form.SetVisibility(vis);
    }

    public void AssociateMaterials()
    {
        foreach (var s in _ctx.Spec.Solids)
        {
            if (s.Material is null || s.IsVoid || !_ctx.Solids.TryGetValue(s.Id, out var e)) continue;
            if (!_ctx.Params.TryGetValue(s.Material, out var fp)) continue;
            var param = e.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM);
            if (param is null) continue;
            var r = Tx.Run(Doc, $"Forge 3D - material {s.Name}", () =>
                _ctx.Mgr.AssociateElementParameterToFamilyParameter(param, fp));
            if (r.Ok) _ctx.Report.Ok("Materiais", s.Name, fp.Definition.Name);
            else _ctx.Report.Fail("Materiais", s.Name, r.ErrorText);
        }
    }

    public void ApplyVoidCuts()
    {
        var voids = _ctx.Spec.Solids.Where(s => s.IsVoid && _ctx.Solids.ContainsKey(s.Id)).ToList();
        if (voids.Count == 0) return;
        var bodies = _ctx.Spec.Solids.Where(s => !s.IsVoid && _ctx.Solids.ContainsKey(s.Id)).ToList();
        foreach (var v in voids)
        {
            var cutter = _ctx.Solids[v.Id];
            var cbb = cutter.get_BoundingBox(null);
            int count = 0;
            foreach (var b in bodies)
            {
                var body = _ctx.Solids[b.Id];
                var bbb = body.get_BoundingBox(null);
                if (cbb is null || bbb is null || !Overlap(cbb, bbb)) continue;
                var r = Tx.Run(Doc, $"Forge 3D - recorte {v.Name}", () =>
                {
                    if (SolidSolidCutUtils.CutExistsBetweenElements(body, cutter, out _)) return;
                    if (!SolidSolidCutUtils.CanElementCutElement(cutter, body, out var reason))
                        throw new InvalidOperationException($"recorte não permitido ({reason})");
                    SolidSolidCutUtils.AddCutBetweenSolids(Doc, body, cutter);
                });
                if (r.Ok) count++;
                else _ctx.Report.Fail(Stage, $"{v.Name} → {b.Name}", r.ErrorText);
            }
            _ctx.Report.Ok(Stage, $"{v.Name}: recorte", $"{count} sólido(s)");
        }
    }

    private static bool Overlap(BoundingBoxXYZ a, BoundingBoxXYZ b) =>
        a.Min.X <= b.Max.X && a.Max.X >= b.Min.X && a.Min.Y <= b.Max.Y && a.Max.Y >= b.Min.Y && a.Min.Z <= b.Max.Z && a.Max.Z >= b.Min.Z;
}
