using ApexSetup.Core.Security;

namespace ApexSetup.Core.Pipeline;

/// <summary>
/// Défait une installation INTERROMPUE (processus tué, pas un échec
/// normal du pipeline — celui-là se roll back déjà lui-même en mémoire,
/// voir <see cref="InstallationPipeline"/>). Fonctionne uniquement à
/// partir de faits observables sur le DISQUE (fichiers <c>.installation-backup</c>
/// laissés par <c>InstallationStage</c>/<c>ConfigurationStage</c>, eux-mêmes
/// écrits AVANT toute modification — donc physiquement survivants à un
/// crash), jamais depuis un état en mémoire qui n'existe plus après un
/// redémarrage du processus.
/// </summary>
public static class InstallationRecovery
{
    /// <summary>
    /// Restaure chaque fichier sauvegardé à son état d'origine, supprime
    /// tout fichier introduit par l'exécution interrompue (aucune
    /// sauvegarde correspondante = nouveau), puis efface l'état persisté.
    /// Lève si l'état n'est pas <see cref="RecoveryState.InterruptedNeedsRollback"/>
    /// — un appelant qui invoque ceci sans avoir d'abord classifié
    /// commet une erreur de programmation, pas une situation à deviner.
    /// </summary>
    public static void RollbackInterrupted(string destinationDirectory)
    {
        var assessment = RecoveryClassifier.Classify(destinationDirectory);
        if (assessment.State != RecoveryState.InterruptedNeedsRollback || assessment.PersistedState is not { } state)
        {
            throw new InvalidOperationException(
                $"RollbackInterrupted appelé alors que l'état est {assessment.State}, pas InterruptedNeedsRollback : {assessment.Detail}");
        }

        var artifactsDirectory = Path.Combine(destinationDirectory, "artifacts");
        if (Directory.Exists(artifactsDirectory))
        {
            foreach (var artifactId in state.RequiredArtifactIds)
            {
                if (!PathSafety.IsSafeFileName(artifactId, out _))
                {
                    // Un identifiant devenu dangereux entre l'écriture de
                    // l'état et sa lecture est ignoré plutôt que suivi —
                    // jamais une combinaison de chemin non sûre, même en
                    // récupération.
                    continue;
                }
                var finalPath = PathSafety.SafeCombine(artifactsDirectory, artifactId);
                RestoreOrRemove(finalPath);
                TryDelete(finalPath + ".installing");
            }
        }

        var configPath = Path.Combine(destinationDirectory, "apex-user-config.json");
        RestoreOrRemove(configPath);
        TryDelete(configPath + ".writing");

        InstallationStateStore.Delete(destinationDirectory);
    }

    private static void RestoreOrRemove(string finalPath)
    {
        var backupPath = finalPath + ".installation-backup";
        if (File.Exists(backupPath))
        {
            File.Move(backupPath, finalPath, overwrite: true);
        }
        else
        {
            TryDelete(finalPath);
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (IOException)
        {
            // Best-effort, même discipline que le reste du moteur.
        }
    }
}
