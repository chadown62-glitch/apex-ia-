using ApexSetup.Core.Integrity;
using ApexSetup.Core.Pipeline;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 2 du pipeline : re-vérifie l'intégrité SHA-256 de chaque
/// artefact téléchargé, À SON EMPLACEMENT FINAL SUR DISQUE — défense en
/// profondeur DISTINCTE de la vérification déjà faite par
/// <see cref="Download.HttpArtifactDownloader"/> avant promotion (celle-ci
/// protège contre un transfert corrompu ; celle-ci protège contre une
/// corruption survenue APRÈS la promotion — disque défaillant, artefact
/// déjà présent d'une exécution antérieure jamais re-téléchargé, etc.).
/// Aucune installation d'un artefact dont l'intégrité n'est pas
/// RE-CONFIRMÉE ici.
/// </summary>
public sealed class IntegrityVerificationStage : IInstallationStage
{
    public string Name => "integrity_verification";

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var downloadedPaths = context.GetRequiredItem<Dictionary<string, string>>(ContextKeys.DownloadedArtifactPaths);

        foreach (var artifact in context.Plan.RequiredArtifacts)
        {
            if (!downloadedPaths.TryGetValue(artifact.ArtifactId, out var path))
            {
                return StageOutcome.Failed($"artefact « {artifact.ArtifactId} » absent des téléchargements — étape Download incohérente");
            }

            var result = await Sha256Verifier.VerifyFileAsync(path, artifact.Sha256, cancellationToken).ConfigureAwait(false);
            if (!result.IsValid)
            {
                return StageOutcome.Failed(
                    $"artefact « {artifact.ArtifactId} » : intégrité invalide à l'emplacement final " +
                    $"({result.Outcome}, attendu {artifact.Sha256}, obtenu {result.ActualSha256 ?? "(illisible)"})");
            }
        }

        return StageOutcome.Ok();
    }

    /// <summary>Pure vérification, aucune modification d'état — rien à défaire.</summary>
    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
}
