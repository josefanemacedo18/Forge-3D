using System.Text.Json;
using System.Text.Json.Nodes;
using Forge3D.Core.Spec;
using Xunit;

namespace Forge3D.Core.Tests;

public class SpecTests
{
    public static string FixturesDir => Path.Combine(AppContext.BaseDirectory, "Fixtures");

    public static IEnumerable<object[]> AllFixtures() =>
        Directory.GetFiles(FixturesDir, "*.json").Select(p => new object[] { Path.GetFileName(p) });

    [Theory]
    [MemberData(nameof(AllFixtures))]
    public void Engine_specs_parse_and_validate_in_csharp(string file)
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, file));
        var errors = SpecValidator.Validate(spec);
        Assert.True(errors.Count == 0, string.Join("\n", errors));
    }

    [Theory]
    [MemberData(nameof(AllFixtures))]
    public void Plane_tree_reproduces_positions(string file)
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, file));
        var values = SpecMath.ParameterValues(spec);
        var pos = SpecMath.PlanePositions(spec, values);
        foreach (var pl in spec.Planes) Assert.Equal(pl.Position, pos[pl.Id], 2);
    }

    [Fact]
    public void Table_has_expected_semantic_parameters()
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, "mesa.json"));
        var names = spec.Parameters.Select(p => p.Name).ToHashSet();
        foreach (var n in new[] { "Largura", "Profundidade", "Altura", "Espessura do Tampo", "Seção da Perna", "Recuo X", "Recuo Y" })
            Assert.Contains(n, names);
        Assert.Equal("C", spec.Family.AchievedLevel);
    }

    [Fact]
    public void Changing_width_moves_planes_but_keeps_leg_section()
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, "mesa.json"));
        var largura = spec.Parameters.Single(p => p.Name == "Largura");
        var values = SpecMath.ParameterValues(spec, new Dictionary<string, double> { [largura.Id] = 2000 });
        var pos = SpecMath.PlanePositions(spec, values);
        var right = spec.Planes.Single(p => p.Reference == "right");
        Assert.Equal(1000, pos[right.Id], 6);
        var leg = spec.Solids.First(s => s.Name.StartsWith("Perna"));
        var width = pos[leg.X![1]] - pos[leg.X[0]];
        Assert.Equal(50, width, 6);
    }

    [Theory]
    [MemberData(nameof(AllFixtures))]
    public void Safe_ranges_contain_the_value_and_respect_revit_minimum(string file)
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, file));
        foreach (var p in spec.Parameters.Where(p => p.Min is not null || p.Max is not null))
        {
            Assert.True(p.IsWithinSafeRange(p.NumberValue), $"{file}: {p.Name} = {p.NumberValue} fora de {p.SafeRangeText}");
            if (p.Min is { } lo) Assert.True(lo >= 1.0, $"{file}: {p.Name} com mínimo {lo} < 1 mm");
        }
    }

    [Fact]
    public void Safe_range_text_is_readable()
    {
        var p = new ParameterSpec { Id = "p", Name = "X", Min = 12, Max = 40.25 };
        Assert.Equal("12 – 40,3".Replace(",", System.Globalization.CultureInfo.CurrentCulture.NumberFormat.NumberDecimalSeparator), p.SafeRangeText);
        Assert.True(p.IsWithinSafeRange(20));
        Assert.False(p.IsWithinSafeRange(41));
        Assert.False(p.IsWithinSafeRange(11));
        Assert.Equal("", new ParameterSpec { Id = "q", Name = "Y" }.SafeRangeText);
    }

    [Fact]
    public void Thin_sheets_fixture_has_no_part_thinner_than_revit_accepts()
    {
        var spec = SpecLoader.LoadValidated(Path.Combine(FixturesDir, "chapas_finas.json"));
        foreach (var s in spec.Solids.Where(s => s.Kind == SolidKinds.Extrusion && s.Frame is not null))
            Assert.True(s.Depth >= 1.0, $"{s.Id}: profundidade {s.Depth}");
    }

    private static FamilySpec Mutate(string file, Action<JsonNode> change)
    {
        var node = JsonNode.Parse(File.ReadAllText(Path.Combine(FixturesDir, file)))!;
        change(node);
        return SpecLoader.Parse(node.ToJsonString());
    }

    [Fact]
    public void Validator_rejects_broken_specs()
    {
        var shifted = Mutate("mesa.json", n => n["planes"]![0]!["position"] = n["planes"]![0]!["position"]!.GetValue<double>() + 7);
        Assert.NotEmpty(SpecValidator.Validate(shifted));

        var cycle = Mutate("mesa.json", n => n["planes"]![0]!["parent"] = n["planes"]![0]!["id"]!.GetValue<string>());
        Assert.NotEmpty(SpecValidator.Validate(cycle));

        var dupName = Mutate("mesa.json", n =>
        {
            var ps = n["parameters"]!.AsArray();
            ps[^1]!["name"] = ps[0]!["name"]!.GetValue<string>();
        });
        Assert.NotEmpty(SpecValidator.Validate(dupName));

        var badFormula = Mutate("mesa.json", n =>
        {
            foreach (var p in n["parameters"]!.AsArray())
                if (p!["formula"] is JsonValue) { p["formula"] = "Inexistente / 2"; break; }
        });
        Assert.NotEmpty(SpecValidator.Validate(badFormula));

        var shortSeg = Mutate("prateleira_furos.json", n =>
        {
            var seg = n["solids"]![0]!["loops"]![0]![0]!;
            var start = seg["start"]!.AsArray();
            seg["end"] = new JsonArray(start[0]!.GetValue<double>() + 0.2, start[1]!.GetValue<double>());
        });
        Assert.NotEmpty(SpecValidator.Validate(shortSeg));

        var wrongVersion = Mutate("mesa.json", n => n["version"] = 99);
        Assert.Contains(SpecValidator.Validate(wrongVersion), e => e.Contains("Versão"));
    }

    [Fact]
    public void Loader_reports_invalid_json_in_portuguese()
    {
        var ex = Assert.Throws<SpecException>(() => SpecLoader.Parse("{ isto não é json"));
        Assert.Contains("FamilySpec", ex.Message);
    }

    [Fact]
    public void Roundtrip_serialization_keeps_spec_valid()
    {
        var spec = SpecLoader.Load(Path.Combine(FixturesDir, "prateleira_furos.json"));
        var again = SpecLoader.Parse(SpecLoader.Serialize(spec));
        Assert.Empty(SpecValidator.Validate(again));
        Assert.Contains(again.Solids, s => s.IsVoid);
    }
}
