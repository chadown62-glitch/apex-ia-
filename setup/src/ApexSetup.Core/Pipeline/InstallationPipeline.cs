using ApexSetup.Core.Planning;

namespace ApexSetup.Core.Pipeline;

/// <summary>Résultat de l'exécution complète du pipeline.</summary>
public sealed record PipelineResult
{
    public required bool Success { get; init; }
    public required IReadOnlyList<string> ExecutedStageNames { get; init; }
    public string? FailedStageName { get; init; }
    public string? FailureDetail { get; init; }
    public required IReadOnlyList<string> RolledBackStageNames { get; init; }

    /// <summary>
    /// TROUVÉ EN REVUE ADVERSARIALE (§13 Error semantics) : un rollback en
    /// échec n'était visible qu'en DÉDUISANT la différence entre
    /// <c>ExecutedStageNames</c> et <c>RolledBackStageNames</c> — jamais
    /// signalé explicitement, contrairement à l'exigence « un rollback
    /// échoué est toujours signalé comme tel ». Toujours présent
    /// (éventuellement vide), jamais null.
    /// </summary>
    public required IReadOnlyList<string> FailedRollbackStageNames { get; init; }

    public static PipelineResult Succeeded(IReadOnlyList<string> executed) => new()
    {
        Success = true, ExecutedStageNames = executed, RolledBackStageNames = [], FailedRollbackStageNames = [],
    };

    public static PipelineResult Failed(
        string failedStage, string detail, IReadOnlyList<string> executedBeforeFailure,
        IReadOnlyList<string> rolledBack, IReadOnlyList<string> failedRollbacks) => new()
    {
        Success = false,
        ExecutedStageNames = executedBeforeFailure,
        FailedStageName = failedStage,
        FailureDetail = detail,
        RolledBackStageNames = rolledBack,
        FailedRollbackStageNames = failedRollbacks,
    };

    /// <summary>Refus explicite AVANT toute exécution (ex. verrou de concurrence déjà tenu) — jamais un « échec de la première étape » trompeur.</summary>
    public static PipelineResult Refused(string reason) => new()
    {
        Success = false, ExecutedStageNames = [], FailedStageName = null,
        FailureDetail = reason, RolledBackStageNames = [], FailedRollbackStageNames = [],
    };
}

/// <summary>
/// Orchestrateur transactionnel : exécute les étapes dans l'ordre ; à la
/// première étape en échec, annule (dans l'ordre INVERSE) toutes les
/// étapes déjà exécutées avec succès — jamais l'étape en échec elle-même
/// (rien n'y a été fait avec succès à défaire). Le rollback d'une étape
/// est BEST-EFFORT : une exception pendant le rollback d'une étape
/// n'empêche jamais le rollback des étapes précédentes.
///
/// CORRIGÉ EN REVUE ADVERSARIALE :
/// §7 (concurrence) — un <see cref="InstallationLock"/> exclusif sur
///   <c>context.DestinationDirectory</c> est acquis avant toute étape ;
///   une seconde exécution simultanée est REFUSÉE explicitement, jamais
///   laissée produire un résultat indéterministe.
/// §3/§4/§14 (crash safety / recovery / état transactionnel) — l'état
///   (<see cref="InstallationState"/>) est persisté ATOMIQUEMENT après
///   CHAQUE étape réussie — voir <see cref="RecoveryClassifier"/> pour son
///   exploitation après un redémarrage du processus.
/// </summary>
public sealed class InstallationPipeline(IReadOnlyList<IInstallationStage> stages)
{
    public async Task<PipelineResult> RunAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        using var installLock = InstallationLock.TryAcquire(context.DestinationDirectory);
        if (installLock is null)
        {
            return PipelineResult.Refused(
                $"une autre installation est déjà en cours pour « {context.DestinationDirectory} » (verrou tenu)");
        }

        var executed = new List<IInstallationStage>();
        var completedNames = new List<string>();
        var planFingerprint = PlanFingerprint.Compute(context.Plan);

        foreach (var stage in stages)
        {
            cancellationToken.ThrowIfCancellationRequested();

            StageOutcome outcome;
            try
            {
                outcome = await stage.ExecuteAsync(context, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                outcome = StageOutcome.Failed($"exception non gérée : {ex.GetType().Name}: {ex.Message}");
            }

            if (!outcome.Success)
            {
                var (rolledBack, failedRollbacks) = await RollbackAsync(executed, context, cancellationToken).ConfigureAwait(false);
                // Rollback complet (ou tenté) -> aucun état résiduel ne
                // doit laisser croire qu'une installation est encore en
                // cours à cette destination.
                InstallationStateStore.Delete(context.DestinationDirectory);
                return PipelineResult.Failed(
                    stage.Name,
                    outcome.ErrorMessage ?? "échec sans détail fourni",
                    executed.Select(s => s.Name).ToList(),
                    rolledBack,
                    failedRollbacks);
            }

            executed.Add(stage);
            completedNames.Add(stage.Name);

            var phase = stage.Name == "commit" ? InstallationLifecyclePhase.Committed : InstallationLifecyclePhase.InProgress;
            await InstallationStateStore.WriteAsync(
                context.DestinationDirectory,
                new InstallationState
                {
                    PlanFingerprint = planFingerprint,
                    RequiredArtifactIds = context.Plan.RequiredArtifacts.Select(a => a.ArtifactId).ToList(),
                    CompletedStageNames = completedNames.ToList(),
                    Phase = phase,
                    UpdatedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
                },
                cancellationToken).ConfigureAwait(false);
        }

        return PipelineResult.Succeeded(executed.Select(s => s.Name).ToList());
    }

    private static async Task<(IReadOnlyList<string> rolledBack, IReadOnlyList<string> failedRollbacks)> RollbackAsync(
        IReadOnlyList<IInstallationStage> executed, InstallationContext context, CancellationToken cancellationToken)
    {
        var rolledBack = new List<string>();
        var failedRollbacks = new List<string>();
        for (var i = executed.Count - 1; i >= 0; i--)
        {
            try
            {
                await executed[i].RollbackAsync(context, cancellationToken).ConfigureAwait(false);
                rolledBack.Add(executed[i].Name);
            }
            catch
            {
                // Best-effort par conception : une étape dont le rollback
                // échoue ne doit jamais empêcher les étapes PRÉCÉDENTES
                // d'être annulées à leur tour — mais elle est désormais
                // signalée explicitement (voir PipelineResult.FailedRollbackStageNames).
                failedRollbacks.Add(executed[i].Name);
            }
        }
        return (rolledBack, failedRollbacks);
    }
}
