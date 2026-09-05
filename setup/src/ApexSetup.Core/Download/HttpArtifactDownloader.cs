using System.Net;
using System.Net.Http.Headers;
using ApexSetup.Core.Integrity;

namespace ApexSetup.Core.Download;

/// <summary>
/// Téléchargement réel via <see cref="HttpClient"/> — jamais directement
/// vers le fichier final (toujours <c>{Destination}.part</c> d'abord),
/// reprise par en-tête <c>Range</c> si un <c>.part</c> existe déjà,
/// intégrité SHA-256 vérifiée AVANT toute promotion (renommage atomique),
/// fichier <c>.part</c> supprimé immédiatement en cas d'incompatibilité de
/// hash (jamais conservé pour une « reprise » qui ne réparerait rien) mais
/// CONSERVÉ en cas d'erreur réseau/timeout (reprise possible au prochain
/// appel).
/// </summary>
public sealed class HttpArtifactDownloader(HttpClient httpClient) : IArtifactDownloader
{
    public async Task<ArtifactDownloadResult> DownloadAsync(ArtifactDownloadRequest request, CancellationToken cancellationToken = default)
    {
        var partPath = request.DestinationPath + ".part";
        var directory = Path.GetDirectoryName(Path.GetFullPath(request.DestinationPath));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var existingLength = File.Exists(partPath) ? new FileInfo(partPath).Length : 0L;

        using var httpRequest = new HttpRequestMessage(HttpMethod.Get, request.SourceUrl);
        if (existingLength > 0)
        {
            httpRequest.Headers.Range = new RangeHeaderValue(existingLength, null);
        }

        HttpResponseMessage response;
        try
        {
            response = await httpClient
                .SendAsync(httpRequest, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                .ConfigureAwait(false);
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return ArtifactDownloadResult.Failure(
                ArtifactDownloadOutcome.Timeout, "délai dépassé lors de la connexion", ResumablePath(partPath));
        }
        catch (HttpRequestException ex)
        {
            return ArtifactDownloadResult.Failure(
                ArtifactDownloadOutcome.NetworkError, ex.Message, ResumablePath(partPath));
        }

        using (response)
        {
            if (existingLength > 0 && response.StatusCode != HttpStatusCode.PartialContent)
            {
                // Le serveur n'a pas honoré la reprise — redémarrer
                // proprement depuis zéro plutôt que d'ajouter du contenu
                // neuf à la suite d'un fichier partiel incohérent.
                File.Delete(partPath);
                existingLength = 0;
            }

            if (!response.IsSuccessStatusCode)
            {
                return ArtifactDownloadResult.Failure(
                    ArtifactDownloadOutcome.NetworkError, $"code HTTP {(int)response.StatusCode}", ResumablePath(partPath));
            }

            try
            {
                await using var httpStream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
                await using var fileStream = new FileStream(
                    partPath,
                    existingLength > 0 ? FileMode.Append : FileMode.Create,
                    FileAccess.Write, FileShare.None);
                await httpStream.CopyToAsync(fileStream, cancellationToken).ConfigureAwait(false);
            }
            catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                return ArtifactDownloadResult.Failure(
                    ArtifactDownloadOutcome.Timeout,
                    "délai dépassé pendant le transfert — fichier partiel conservé pour reprise",
                    ResumablePath(partPath));
            }
            catch (IOException ex)
            {
                return ArtifactDownloadResult.Failure(
                    ArtifactDownloadOutcome.NetworkError,
                    $"connexion interrompue : {ex.Message} — fichier partiel conservé pour reprise",
                    ResumablePath(partPath));
            }
        }

        var integrity = await Sha256Verifier.VerifyFileAsync(partPath, request.ExpectedSha256, cancellationToken).ConfigureAwait(false);
        if (!integrity.IsValid)
        {
            // Fichier complet mais corrompu : une reprise ne répare rien —
            // supprimer plutôt que de laisser un .part trompeur.
            File.Delete(partPath);
            return ArtifactDownloadResult.Failure(
                ArtifactDownloadOutcome.HashMismatch,
                $"attendu {request.ExpectedSha256}, obtenu {integrity.ActualSha256 ?? "(illisible)"}",
                resumablePartialPath: null);
        }

        File.Move(partPath, request.DestinationPath, overwrite: true);
        return ArtifactDownloadResult.Success(request.DestinationPath, integrity.ActualSha256!);
    }

    private static string? ResumablePath(string partPath) => File.Exists(partPath) ? partPath : null;
}
