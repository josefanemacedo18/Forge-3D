using Autodesk.Revit.UI;
using Forge3D.Core.Spec;

namespace Forge3D.Revit.UI;

/// <summary>Diálogos nativos do Revit (TaskDialog) em português.</summary>
internal static class Dialogs
{
    public const string Title = "Forge 3D";

    public static void Error(string instruction, string content, IEnumerable<string>? details = null)
    {
        var td = new TaskDialog(Title)
        {
            MainIcon = TaskDialogIcon.TaskDialogIconError,
            MainInstruction = instruction,
            MainContent = content,
            CommonButtons = TaskDialogCommonButtons.Close,
        };
        var list = details?.Where(d => !string.IsNullOrWhiteSpace(d)).Take(25).ToList();
        if (list is { Count: > 0 }) td.ExpandedContent = string.Join(Environment.NewLine, list);
        td.Show();
    }

    public static void Info(string instruction, string content)
    {
        new TaskDialog(Title) { MainInstruction = instruction, MainContent = content, CommonButtons = TaskDialogCommonButtons.Ok }.Show();
    }

    /// <summary>
    /// Pergunta exigida pelo usuário: quando o Nível C não é alcançado, avisa e
    /// pergunta se pode gerar em Nível B, mostrando os motivos.
    /// </summary>
    public static bool AskSwitchToLevelB(FamilySpec spec)
    {
        var td = new TaskDialog(Title)
        {
            MainIcon = TaskDialogIcon.TaskDialogIconWarning,
            MainInstruction = "Não foi possível parametrizar todo o objeto em Nível C.",
            MainContent = $"Cobertura em Nível C: {spec.Report.ParametricCoverage:P0} do volume.\n\n" +
                          "Posso gerar a família em Nível B? As peças que alcançaram o Nível C continuam com seus " +
                          "parâmetros; as demais ficam fixas ou acompanham o envelope.",
            ExpandedContent = "Peças que impediram o Nível C:\n" + string.Join("\n", spec.Report.LevelCBlockers.Select(b => "• " + b)),
            CommonButtons = TaskDialogCommonButtons.None,
            AllowCancellation = true,
        };
        td.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "Sim, gerar em Nível B");
        td.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "Não, voltar sem criar a família");
        return td.Show() == TaskDialogResult.CommandLink1;
    }
}
