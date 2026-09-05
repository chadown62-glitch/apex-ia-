namespace ApexSetup.Core.Pipeline;

/// <summary>Les 4 états explicitement exigés par la revue adversariale (§4 Recovery).</summary>
public enum RecoveryState
{
    /// <summary>Rien à cette destination — ni marqueur de commit, ni état persisté.</summary>
    NoPriorInstallation,

    /// <summary>Marqueur de commit présent et cohérent avec l'état persisté (ou absence d'état, cas d'une installation antérieure à cette fonctionnalité).</summary>
    Completed,

    /// <summary>État persisté « InProgress » sans marqueur de commit — le processus a été interrompu avant Commit.</summary>
    InterruptedNeedsRollback,

    /// <summary>État illisible, ou incohérence entre marqueur de commit et état persisté — nécessite une intervention, jamais une supposition.</summary>
    Corrupt,
}

public sealed record RecoveryAssessment
{
    public required RecoveryState State { get; init; }
    public InstallationState? PersistedState { get; init; }
    public required string Detail { get; init; }
}

/// <summary>
/// Classifie une destination AVANT toute action — jamais de suppositions :
/// une observation directe du disque (marqueur de commit + état persisté),
/// jamais une inférence heuristique sur le contenu du répertoire
/// <c>artifacts/</c> lui-même (qui pourrait légitimement contenir des
/// fichiers d'une tout autre origine).
/// </summary>
public static class RecoveryClassifier
{
    public static RecoveryAssessment Classify(string destinationDirectory)
    {
        var commitMarkerPath = Path.Combine(destinationDirectory, ".apex-setup-installed");
        var hasCommitMarker = File.Exists(commitMarkerPath);
        var stateFilePath = Path.Combine(destinationDirectory, InstallationStateStore.FileName);
        var stateFilePresent = File.Exists(stateFilePath);

        if (!hasCommitMarker && !stateFilePresent)
        {
            return new RecoveryAssessment
            {
                State = RecoveryState.NoPriorInstallation,
                Detail = "aucune trace d'installation précédente à cette destination",
            };
        }

        InstallationState? state = null;
        if (stateFilePresent)
        {
            state = InstallationStateStore.TryRead(destinationDirectory);
            if (state is null)
            {
                return new RecoveryAssessment
                {
                    State = RecoveryState.Corrupt,
                    Detail = $"« {InstallationStateStore.FileName} » présent mais illisible ou structurellement invalide",
                };
            }
        }

        if (hasCommitMarker)
        {
            if (state is null || state.Phase == InstallationLifecyclePhase.Committed)
            {
                return new RecoveryAssessment
                {
                    State = RecoveryState.Completed,
                    PersistedState = state,
                    Detail = "marqueur de commit présent, cohérent avec l'état persisté",
                };
            }
            return new RecoveryAssessment
            {
                State = RecoveryState.Corrupt,
                PersistedState = state,
                Detail = "marqueur de commit présent MAIS état persisté encore « InProgress » — incohérence",
            };
        }

        // Pas de marqueur de commit à ce stade.
        if (state is not null && state.Phase == InstallationLifecyclePhase.InProgress)
        {
            return new RecoveryAssessment
            {
                State = RecoveryState.InterruptedNeedsRollback,
                PersistedState = state,
                Detail = $"installation interrompue après les étapes : {string.Join(", ", state.CompletedStageNames)} — jamais committée",
            };
        }

        return new RecoveryAssessment
        {
            State = RecoveryState.Corrupt,
            PersistedState = state,
            Detail = "état persisté marqué Committed mais aucun marqueur de commit sur disque — incohérence",
        };
    }
}
