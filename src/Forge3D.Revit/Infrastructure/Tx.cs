using Autodesk.Revit.DB;

namespace Forge3D.Revit.Infrastructure;

/// <summary>
/// Coleta as falhas do Revit numa transação: avisos são registrados e
/// apagados; qualquer erro desfaz a transação (nada fica pela metade).
/// </summary>
internal sealed class FailureCollector : IFailuresPreprocessor
{
    public List<string> Errors { get; } = new();
    public List<string> Warnings { get; } = new();

    public FailureProcessingResult PreprocessFailures(FailuresAccessor accessor)
    {
        bool hasError = false;
        foreach (var f in accessor.GetFailureMessages())
        {
            var text = f.GetDescriptionText();
            if (f.GetSeverity() == FailureSeverity.Warning)
            {
                Warnings.Add(text);
                accessor.DeleteWarning(f);
            }
            else
            {
                Errors.Add(text);
                hasError = true;
            }
        }
        return hasError ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
    }
}

internal sealed record TxResult(bool Ok, IReadOnlyList<string> Errors, IReadOnlyList<string> Warnings)
{
    public string ErrorText => string.Join("; ", Errors);
}

internal static class Tx
{
    /// <summary>Executa <paramref name="action"/> numa transação isolada.</summary>
    public static TxResult Run(Document doc, string name, Action action)
    {
        var collector = new FailureCollector();
        using var t = new Transaction(doc, name);
        var options = t.GetFailureHandlingOptions();
        options.SetFailuresPreprocessor(collector);
        options.SetClearAfterRollback(true);
        options.SetForcedModalHandling(false);
        t.SetFailureHandlingOptions(options);
        try
        {
            t.Start();
            action();
            var status = t.Commit();
            if (status != TransactionStatus.Committed)
            {
                var errs = collector.Errors.Count > 0 ? collector.Errors : new List<string> { $"transação desfeita ({status})" };
                return new TxResult(false, errs, collector.Warnings);
            }
            return new TxResult(true, collector.Errors, collector.Warnings);
        }
        catch (Exception ex)
        {
            try
            {
                if (t.HasStarted() && !t.HasEnded()) t.RollBack();
            }
            catch
            {
                // a transação pode já ter sido encerrada pelo Revit
            }
            return new TxResult(false, new List<string> { ex.Message }, collector.Warnings);
        }
    }

    /// <summary>
    /// Executa, confirma (para o Revit processar as falhas de regeneração),
    /// roda a verificação opcional e desfaz tudo. Usado no teste de flexão.
    /// </summary>
    public static TxResult Probe(Document doc, string name, Action action, Func<string?>? verify = null)
    {
        using var group = new TransactionGroup(doc, name);
        group.Start();
        try
        {
            var r = Run(doc, name, action);
            if (!r.Ok || verify is null) return r;
            string? problem;
            try
            {
                problem = verify();
            }
            catch (Exception ex)
            {
                problem = ex.Message;
            }
            return problem is null ? r : r with { Ok = false, Errors = r.Errors.Append(problem).ToList() };
        }
        finally
        {
            try
            {
                group.RollBack();
            }
            catch
            {
                // grupo já encerrado
            }
        }
    }
}
