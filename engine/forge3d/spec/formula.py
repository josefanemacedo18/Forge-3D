"""Avaliador do subconjunto de fórmulas do Revit usado pelo Forge 3D.

O motor gera fórmulas apenas com: números sem unidade, nomes de parâmetros,
operadores ``+ - * /`` e parênteses. Nomes de parâmetros começam com letra e
podem conter letras (com acento), dígitos, espaços e ``_``. Não usamos ponto
decimal nas fórmulas geradas (frações viram ``n / d``) para evitar problemas
com o separador decimal do Revit em diferentes idiomas, mas o avaliador aceita
números decimais para ler fórmulas editadas à mão.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

NAME_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿ0-9 _"
_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<op>[-+*/()])|(?P<name>[A-Za-zÀ-ÖØ-öø-ÿ][" + NAME_CHARS + r"]*))"
)


class FormulaError(ValueError):
    """Erro de sintaxe ou de avaliação em uma fórmula."""


@dataclass(frozen=True)
class Token:
    kind: str  # "num", "op", "name"
    text: str


def tokenize(formula: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    text = formula.strip()
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise FormulaError(f"Caractere inesperado na fórmula '{formula}' na posição {pos}.")
        pos = m.end()
        if m.group("num") is not None:
            tokens.append(Token("num", m.group("num")))
        elif m.group("op") is not None:
            tokens.append(Token("op", m.group("op")))
        else:
            tokens.append(Token("name", m.group("name").strip()))
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return tokens


def referenced_names(formula: str) -> list[str]:
    """Nomes de parâmetros citados na fórmula, na ordem em que aparecem."""
    return [t.text for t in tokenize(formula) if t.kind == "name"]


class _Parser:
    """Analisador descendente recursivo: expr := term (('+'|'-') term)*."""

    def __init__(self, tokens: list[Token], resolve: Callable[[str], float], formula: str):
        self.tokens = tokens
        self.i = 0
        self.resolve = resolve
        self.formula = formula

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise FormulaError(f"Fórmula incompleta: '{self.formula}'.")
        self.i += 1
        return tok

    def parse(self) -> float:
        value = self.expr()
        if self.peek() is not None:
            raise FormulaError(f"Sobrou texto na fórmula '{self.formula}' após a posição {self.i}.")
        return value

    def expr(self) -> float:
        value = self.term()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text in "+-":
            self.take()
            rhs = self.term()
            value = value + rhs if tok.text == "+" else value - rhs
        return value

    def term(self) -> float:
        value = self.factor()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text in "*/":
            self.take()
            rhs = self.factor()
            if tok.text == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise FormulaError(f"Divisão por zero na fórmula '{self.formula}'.")
                value = value / rhs
        return value

    def factor(self) -> float:
        tok = self.take()
        if tok.kind == "op" and tok.text == "-":
            return -self.factor()
        if tok.kind == "op" and tok.text == "+":
            return self.factor()
        if tok.kind == "num":
            return float(tok.text)
        if tok.kind == "name":
            return float(self.resolve(tok.text))
        if tok.kind == "op" and tok.text == "(":
            value = self.expr()
            close = self.take()
            if close.kind != "op" or close.text != ")":
                raise FormulaError(f"Parêntese não fechado na fórmula '{self.formula}'.")
            return value
        raise FormulaError(f"Token inesperado '{tok.text}' na fórmula '{self.formula}'.")


def evaluate(formula: str, resolve: Callable[[str], float]) -> float:
    return _Parser(tokenize(formula), resolve, formula).parse()


def evaluate_all(
    values: Mapping[str, float],
    formulas: Mapping[str, str],
) -> dict[str, float]:
    """Avalia todas as fórmulas, resolvendo dependências entre elas.

    ``values`` mapeia nome -> valor dos parâmetros sem fórmula; ``formulas``
    mapeia nome -> fórmula. Detecta referências inexistentes e ciclos.
    """
    result: dict[str, float] = dict(values)
    state: dict[str, int] = {}  # 1 = visitando, 2 = pronto

    def resolve(name: str) -> float:
        if name in formulas:
            visit(name)
            return result[name]
        if name in result:
            return result[name]
        raise FormulaError(f"Parâmetro '{name}' não existe.")

    def visit(name: str) -> None:
        st = state.get(name)
        if st == 2:
            return
        if st == 1:
            raise FormulaError(f"Fórmulas em ciclo envolvendo '{name}'.")
        state[name] = 1
        result[name] = evaluate(formulas[name], resolve)
        state[name] = 2

    for name in formulas:
        visit(name)
    return result


def fraction_formula(name: str, fraction: float, denominator: int = 10000) -> str:
    """Gera ``Nome * n / d`` sem ponto decimal (seguro em qualquer idioma)."""
    if fraction < 0:
        raise ValueError("fraction deve ser >= 0")
    n = int(round(fraction * denominator))
    from math import gcd

    g = gcd(n, denominator) or 1
    n //= g
    d = denominator // g
    if d == 1:
        return f"{name} * {n}" if n != 1 else name
    return f"{name} * {n} / {d}"


def check_names(names: Iterable[str]) -> list[str]:
    """Retorna mensagens de erro para nomes inválidos em fórmulas."""
    errors = []
    pattern = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][" + NAME_CHARS + r"]*$")
    for n in names:
        if not pattern.match(n) or n != n.strip():
            errors.append(f"Nome de parâmetro inválido para fórmulas: '{n}'.")
    return errors
