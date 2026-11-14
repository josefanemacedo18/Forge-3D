import pytest

from forge3d.spec import formula as fx


def test_tokenize_names_with_spaces_and_accents():
    toks = fx.tokenize("Largura / 2 - Seção da Perna")
    assert [t.text for t in toks] == ["Largura", "/", "2", "-", "Seção da Perna"]


def test_evaluate_precedence_and_parentheses():
    vals = {"A": 10.0, "B c": 4.0}
    assert fx.evaluate("A + B c * 2", vals.__getitem__) == 18.0
    assert fx.evaluate("(A + B c) * 2", vals.__getitem__) == 28.0
    assert fx.evaluate("-A + 3", vals.__getitem__) == -7.0


def test_evaluate_all_resolves_chain_and_detects_cycles():
    res = fx.evaluate_all({"Largura": 1200.0}, {"Meia": "Largura / 2", "Quarto": "Meia / 2"})
    assert res["Quarto"] == 300.0
    with pytest.raises(fx.FormulaError):
        fx.evaluate_all({}, {"A": "B + 1", "B": "A + 1"})
    with pytest.raises(fx.FormulaError):
        fx.evaluate_all({}, {"A": "Inexistente * 2"})


def test_fraction_formula_has_no_decimal_point():
    f = fx.fraction_formula("Largura", 0.3725)
    assert "." not in f
    assert abs(fx.evaluate(f, lambda n: 1000.0) - 372.5) < 1e-9


def test_division_by_zero_is_reported():
    with pytest.raises(fx.FormulaError):
        fx.evaluate("A / 0", lambda n: 1.0)
