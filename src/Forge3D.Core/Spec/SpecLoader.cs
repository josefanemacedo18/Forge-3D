using System.Text.Json;

namespace Forge3D.Core.Spec;

/// <summary>Spec inválido: nada deve ser criado no Revit.</summary>
public sealed class SpecException(string message, IReadOnlyList<string>? errors = null) : Exception(message)
{
    public IReadOnlyList<string> Errors { get; } = errors ?? Array.Empty<string>();
}

public static class SpecLoader
{
    public static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = false,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
        WriteIndented = false,
    };

    public static FamilySpec Parse(string json)
    {
        FamilySpec? spec;
        try
        {
            spec = JsonSerializer.Deserialize<FamilySpec>(json, JsonOptions);
        }
        catch (JsonException ex)
        {
            throw new SpecException($"O arquivo do motor não é um FamilySpec válido: {ex.Message}");
        }
        return spec ?? throw new SpecException("O arquivo do motor está vazio.");
    }

    public static FamilySpec Load(string path)
    {
        if (!File.Exists(path)) throw new SpecException($"Arquivo do spec não encontrado: {path}");
        return Parse(File.ReadAllText(path));
    }

    /// <summary>Carrega e valida; lança <see cref="SpecException"/> com todos os erros.</summary>
    public static FamilySpec LoadValidated(string path)
    {
        var spec = Load(path);
        var errors = SpecValidator.Validate(spec);
        if (errors.Count > 0)
            throw new SpecException("O FamilySpec gerado pelo motor é inválido; nada foi criado no Revit.", errors);
        return spec;
    }

    public static string Serialize(FamilySpec spec) => JsonSerializer.Serialize(spec, JsonOptions);
}
