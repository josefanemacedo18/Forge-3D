using Forge3D.Core.Spec;
using Xunit;

namespace Forge3D.Core.Tests;

public class FormulaTests
{
    [Fact]
    public void Tokenizes_names_with_spaces_and_accents()
    {
        var toks = Formula.Tokenize("Largura / 2 - Seção da Perna");
        Assert.Equal(new[] { "Largura", "/", "2", "-", "Seção da Perna" }, toks.Select(t => t.Text));
    }

    [Fact]
    public void Evaluates_precedence_parentheses_and_unary_minus()
    {
        var vals = new Dictionary<string, double> { ["A"] = 10, ["B c"] = 4 };
        Assert.Equal(18, Formula.Evaluate("A + B c * 2", n => vals[n]));
        Assert.Equal(28, Formula.Evaluate("(A + B c) * 2", n => vals[n]));
        Assert.Equal(-7, Formula.Evaluate("-A + 3", n => vals[n]));
        Assert.Equal(372.5, Formula.Evaluate("Largura * 149 / 400", _ => 1000), 9);
    }

    [Fact]
    public void Resolves_chains_and_detects_cycles_and_unknown_names()
    {
        var res = Formula.EvaluateAll(new Dictionary<string, double> { ["Largura"] = 1200 },
            new Dictionary<string, string> { ["Meia"] = "Largura / 2", ["Quarto"] = "Meia / 2" });
        Assert.Equal(300, res["Quarto"]);
        Assert.Throws<FormulaException>(() => Formula.EvaluateAll(new Dictionary<string, double>(),
            new Dictionary<string, string> { ["A"] = "B + 1", ["B"] = "A + 1" }));
        Assert.Throws<FormulaException>(() => Formula.EvaluateAll(new Dictionary<string, double>(),
            new Dictionary<string, string> { ["A"] = "Inexistente * 2" }));
        Assert.Throws<FormulaException>(() => Formula.Evaluate("A / 0", _ => 1));
    }

    [Theory]
    [InlineData("Largura", true)]
    [InlineData("Espessura do Tampo", true)]
    [InlineData("aux X01", true)]
    [InlineData("1Largura", false)]
    [InlineData("Largura-Total", false)]
    [InlineData(" Largura", false)]
    public void Validates_parameter_names(string name, bool ok) => Assert.Equal(ok, Formula.IsValidName(name));
}

public class MeshTopologyTests
{
    [Fact]
    public void Separate_pieces_become_separate_components()
    {
        // Dois tetraedros independentes (vértices 0-3 e 4-7).
        var faces = new List<int[]>
        {
            new[] { 0, 1, 2 }, new[] { 0, 3, 1 }, new[] { 1, 3, 2 }, new[] { 2, 3, 0 },
            new[] { 4, 5, 6 }, new[] { 4, 7, 5 }, new[] { 5, 7, 6 }, new[] { 6, 7, 4 },
        };
        var comps = Forge3D.Core.Mesh.MeshTopology.Components(faces, 8);
        Assert.Equal(2, comps.Count);
        Assert.All(comps, c => Assert.Equal(4, c.Count));
        Assert.Single(Forge3D.Core.Mesh.MeshTopology.Components(faces.Take(4).ToList(), 8));
    }
}
