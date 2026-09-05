using ApexSetup.Core.Pipeline;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 5 : valide le résultat des étapes précédentes AVANT le point de
/// non-retour (Commit).
///
/// PÉRIMÈTRE HONNÊTE de cette phase : cette étape ne valide PAS un
/// Runtime Python fraîchement déployé sous WSL2 (le pipeline de cette
/// phase installe des artefacts modèles, pas un Runtime complet — voir
/// la limitation documentée dans <c>Planning.InstallationPlanBuilder</c>).
/// Elle vérifie deux choses réelles et vérifiables ici :
/// 1. chaque artefact annoncé installé (<c>ContextKeys.InstalledFilePaths</c>)
///    existe RÉELLEMENT sur disque à la taille attendue ;
/// 2. le Runtime déjà en cours d'utilisation (celui interrogé au
///    Preflight) répond TOUJOURS à Doctor/Gate après l'installation —
///    détecte une dégradation provoquée par l'installation elle-même
///    (ex. disque rempli par les artefacts copiés).
/// </summary>
public sealed class ValidationStage(RuntimeAdapter.RuntimeAdapter runtimeAdapter) : IInstallationStage
{
    public string Name => "validation";

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var installedPaths = context.GetRequiredItem<List<string>>(ContextKeys.InstalledFilePaths);
        if (installedPaths.Count != context.Plan.RequiredArtifacts.Count)
        {
            return StageOutcome.Failed(
                $"{installedPaths.Count} artefact(s) installé(s), {context.Plan.RequiredArtifacts.Count} attendu(s) par le plan");
        }

        foreach (var artifact in context.Plan.RequiredArtifacts)
        {
            var expectedPath = Path.Combine(context.DestinationDirectory, "artifacts", artifact.ArtifactId);
            if (!File.Exists(expectedPath))
            {
                return StageOutcome.Failed($"artefact « {artifact.ArtifactId} » annoncé installé mais absent de {expectedPath}");
            }
            var actualSize = new FileInfo(expectedPath).Length;
            if (actualSize != artifact.SizeBytes)
            {
                return StageOutcome.Failed(
                    $"artefact « {artifact.ArtifactId} » : taille sur disque {actualSize} ≠ taille attendue {artifact.SizeBytes}");
            }
        }

        var doctorResult = await runtimeAdapter.GetDoctorAsync(cancellationToken).ConfigureAwait(false);
        if (!doctorResult.IsSuccess)
        {
            return StageOutcome.Failed($"Doctor inexploitable après installation : {doctorResult.Outcome} — {doctorResult.ErrorDetail}");
        }

        var gateResult = await runtimeAdapter.GetGateAsync(cancellationToken).ConfigureAwait(false);
        if (!gateResult.IsSuccess)
        {
            return StageOutcome.Failed($"Gate inexploitable après installation : {gateResult.Outcome} — {gateResult.ErrorDetail}");
        }

        return StageOutcome.Ok();
    }

    /// <summary>Pure vérification, aucune modification d'état — rien à défaire.</summary>
    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
}
