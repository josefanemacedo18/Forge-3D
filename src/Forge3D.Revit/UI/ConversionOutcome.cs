using Forge3D.Core.Spec;

namespace Forge3D.Revit.UI;

/// <summary>Resultado de uma janela de conversão aprovado pelo usuário.</summary>
public sealed record ConversionOutcome(FamilySpec Spec, string SpecPath, string? PreviewPath, string SourcePath, string Route);
