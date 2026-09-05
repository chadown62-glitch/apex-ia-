using ApexSetup.Core.Download;
using ApexSetup.Core.Integrity;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Security;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 1 du pipeline : télécharge chaque <c>RequiredArtifact</c> du plan
/// vers <c>WorkingDirectory</c> (jamais directement vers la destination
/// finale — voir <see cref="Download.HttpArtifactDownloader"/>). Reprise
/// automatique en cas d'erreur réseau/timeout (nombre de tentatives
/// borné) — jamais une boucle infinie.
///
/// CORRIGÉ EN REVUE ADVERSARIALE :
/// §10 (idempotence) — si le fichier final existe déjà dans
///   <c>WorkingDirectory</c> ET que son SHA-256 correspond déjà, AUCUNE
///   requête réseau n'est émise (avant ce correctif, chaque exécution
///   retéléchargeait systématiquement, même un artefact déjà validé).
/// §6 (chemins) — <c>artifact.ArtifactId</c> passe par
///   <see cref="PathSafety.SafeCombine"/>.
/// </summary>
public sealed class DownloadStage(IArtifactDownloader downloader, int maxAttemptsPerArtifact = 3) : IInstallationStage
{
    public string Name => "download";

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var downloadedPaths = new Dictionary<string, string>();

        foreach (var artifact in context.Plan.RequiredArtifacts)
        {
            string destinationPath;
            try
            {
                destinationPath = PathSafety.SafeCombine(context.WorkingDirectory, artifact.ArtifactId);
            }
            catch (UnsafePathException ex)
            {
                CleanUp(downloadedPaths.Values);
                return StageOutcome.Failed($"artefact « {artifact.ArtifactId} » : {ex.Message}");
            }

            // Idempotence : un artefact déjà présent ET déjà valide à cet
            // emplacement n'est jamais retéléchargé.
            if (File.Exists(destinationPath))
            {
                var alreadyValid = await Sha256Verifier.VerifyFileAsync(destinationPath, artifact.Sha256, cancellationToken).ConfigureAwait(false);
                if (alreadyValid.IsValid)
                {
                    downloadedPaths[artifact.ArtifactId] = destinationPath;
                    continue;
                }
                // Présent mais invalide (téléchargement précédent corrompu/incomplet
                // malgré l'absence de .part — ex. coupure entre l'écriture et un
                // .part jamais créé) : ce cache est jetable, un nouveau
                // téléchargement l'écrasera légitimement (voir
                // HttpArtifactDownloader, qui ne promeut jamais un fichier
                // invalide de toute façon).
            }

            var request = new ArtifactDownloadRequest
            {
                SourceUrl = new Uri(artifact.SourceUrl),
                DestinationPath = destinationPath,
                ExpectedSha256 = artifact.Sha256,
                ExpectedSizeBytes = artifact.SizeBytes,
            };

            ArtifactDownloadResult? result = null;
            for (var attempt = 1; attempt <= maxAttemptsPerArtifact; attempt++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                result = await downloader.DownloadAsync(request, cancellationToken).ConfigureAwait(false);
                if (result.IsSuccess)
                {
                    break;
                }
                if (result.Outcome == ArtifactDownloadOutcome.HashMismatch)
                {
                    // Pas de reprise sensée sur un contenu corrompu à la
                    // source — retenter identiquement ne changerait rien
                    // tant que la source elle-même n'a pas changé, mais on
                    // laisse la borne de tentatives faire foi (un serveur
                    // pourrait servir un contenu différent au retry).
                    continue;
                }
                // NetworkError/Timeout : le fichier .part est conservé,
                // la prochaine tentative reprend depuis là où elle s'est arrêtée.
            }

            if (result is null || !result.IsSuccess)
            {
                // Auto-nettoyage : les artefacts déjà téléchargés PENDANT
                // CETTE MÊME exécution ne doivent jamais rester orphelins.
                // Le pipeline n'appellera jamais RollbackAsync sur cette
                // étape (elle n'a pas retourné Success) — c'est donc à
                // elle seule de défaire ce qu'elle a commencé.
                CleanUp(downloadedPaths.Values);
                if (result?.ResumablePartialPath is { } resumable)
                {
                    TryDelete(resumable);
                }
                return StageOutcome.Failed(
                    $"artefact « {artifact.ArtifactId} » : échec après {maxAttemptsPerArtifact} tentative(s) — " +
                    $"{result?.Outcome} : {result?.ErrorDetail}");
            }

            downloadedPaths[artifact.ArtifactId] = result.FinalPath!;
        }

        context.SetItem(ContextKeys.DownloadedArtifactPaths, downloadedPaths);
        return StageOutcome.Ok();
    }

    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        if (context.TryGetItem<Dictionary<string, string>>(ContextKeys.DownloadedArtifactPaths, out var paths))
        {
            foreach (var path in paths!.Values)
            {
                TryDelete(path);
                TryDelete(path + ".part");
            }
        }
        return Task.CompletedTask;
    }

    private static void CleanUp(IEnumerable<string> paths)
    {
        foreach (var path in paths)
        {
            TryDelete(path);
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
            // Best-effort — voir InstallationPipeline.RollbackAsync.
        }
    }
}
