using System.Globalization;
using System.Text;

namespace Forge3D.Core.Spec;

/// <summary>Erro de sintaxe ou de avaliação de fórmula.</summary>
public sealed class FormulaException(string message) : Exception(message);

/// <summary>
/// Subconjunto de fórmulas do Revit usado pelo Forge 3D: números sem unidade,
/// nomes de parâmetros (letras com acento, dígitos, espaços e "_", começando
/// com letra), + - * / e parênteses. Mesmo comportamento do motor Python.
/// </summary>
public static class Formula
{
    public enum TokenKind { Number, Operator, Name }

    public readonly record struct Token(TokenKind Kind, string Text);

    public static bool IsNameStart(char c) => char.IsLetter(c);

    public static bool IsNameChar(char c) => char.IsLetterOrDigit(c) || c == ' ' || c == '_';

    public static List<Token> Tokenize(string formula)
    {
        var tokens = new List<Token>();
        var s = formula.Trim();
        int i = 0;
        while (i < s.Length)
        {
            char c = s[i];
            if (char.IsWhiteSpace(c)) { i++; continue; }
            if (char.IsDigit(c))
            {
                int start = i;
                while (i < s.Length && char.IsDigit(s[i])) i++;
                if (i < s.Length && s[i] == '.' && i + 1 < s.Length && char.IsDigit(s[i + 1]))
                {
                    i++;
                    while (i < s.Length && char.IsDigit(s[i])) i++;
                }
                tokens.Add(new Token(TokenKind.Number, s[start..i]));
                continue;
            }
            if ("+-*/()".IndexOf(c) >= 0)
            {
                tokens.Add(new Token(TokenKind.Operator, c.ToString()));
                i++;
                continue;
            }
            if (IsNameStart(c))
            {
                int start = i;
                while (i < s.Length && IsNameChar(s[i])) i++;
                tokens.Add(new Token(TokenKind.Name, s[start..i].Trim()));
                continue;
            }
            throw new FormulaException($"Caractere inesperado na fórmula '{formula}' na posição {i}.");
        }
        return tokens;
    }

    public static List<string> ReferencedNames(string formula) =>
        Tokenize(formula).Where(t => t.Kind == TokenKind.Name).Select(t => t.Text).ToList();

    public static double Evaluate(string formula, Func<string, double> resolve)
    {
        var parser = new Parser(Tokenize(formula), resolve, formula);
        return parser.Parse();
    }

    /// <summary>Avalia todas as fórmulas (por nome), resolvendo dependências e detectando ciclos.</summary>
    public static Dictionary<string, double> EvaluateAll(
        IReadOnlyDictionary<string, double> values, IReadOnlyDictionary<string, string> formulas)
    {
        var result = new Dictionary<string, double>(values);
        var state = new Dictionary<string, int>();

        double Resolve(string name)
        {
            if (formulas.ContainsKey(name)) { Visit(name); return result[name]; }
            if (result.TryGetValue(name, out var v)) return v;
            throw new FormulaException($"Parâmetro '{name}' não existe.");
        }

        void Visit(string name)
        {
            state.TryGetValue(name, out var st);
            if (st == 2) return;
            if (st == 1) throw new FormulaException($"Fórmulas em ciclo envolvendo '{name}'.");
            state[name] = 1;
            result[name] = Evaluate(formulas[name], Resolve);
            state[name] = 2;
        }

        foreach (var name in formulas.Keys) Visit(name);
        return result;
    }

    public static bool IsValidName(string name)
    {
        if (string.IsNullOrEmpty(name) || name != name.Trim() || !IsNameStart(name[0])) return false;
        return name.All(IsNameChar);
    }

    private sealed class Parser(List<Token> tokens, Func<string, double> resolve, string formula)
    {
        private int _i;

        private Token? Peek() => _i < tokens.Count ? tokens[_i] : null;

        private Token Take()
        {
            var t = Peek() ?? throw new FormulaException($"Fórmula incompleta: '{formula}'.");
            _i++;
            return t;
        }

        public double Parse()
        {
            var v = Expr();
            if (Peek() is not null) throw new FormulaException($"Sobrou texto na fórmula '{formula}'.");
            return v;
        }

        private double Expr()
        {
            var v = Term();
            while (Peek() is { Kind: TokenKind.Operator } t && (t.Text == "+" || t.Text == "-"))
            {
                Take();
                var r = Term();
                v = t.Text == "+" ? v + r : v - r;
            }
            return v;
        }

        private double Term()
        {
            var v = Factor();
            while (Peek() is { Kind: TokenKind.Operator } t && (t.Text == "*" || t.Text == "/"))
            {
                Take();
                var r = Factor();
                if (t.Text == "*") v *= r;
                else
                {
                    if (r == 0) throw new FormulaException($"Divisão por zero na fórmula '{formula}'.");
                    v /= r;
                }
            }
            return v;
        }

        private double Factor()
        {
            var t = Take();
            switch (t.Kind)
            {
                case TokenKind.Operator when t.Text == "-": return -Factor();
                case TokenKind.Operator when t.Text == "+": return Factor();
                case TokenKind.Number: return double.Parse(t.Text, CultureInfo.InvariantCulture);
                case TokenKind.Name: return resolve(t.Text);
                case TokenKind.Operator when t.Text == "(":
                    var v = Expr();
                    var close = Take();
                    if (close.Kind != TokenKind.Operator || close.Text != ")")
                        throw new FormulaException($"Parêntese não fechado na fórmula '{formula}'.");
                    return v;
                default:
                    throw new FormulaException($"Token inesperado '{t.Text}' na fórmula '{formula}'.");
            }
        }
    }

    /// <summary>Normaliza espaços para comparação de nomes.</summary>
    public static string NormalizeName(string name)
    {
        var sb = new StringBuilder();
        bool space = false;
        foreach (var c in name.Trim())
        {
            if (char.IsWhiteSpace(c)) { if (!space) sb.Append(' '); space = true; }
            else { sb.Append(c); space = false; }
        }
        return sb.ToString();
    }
}
