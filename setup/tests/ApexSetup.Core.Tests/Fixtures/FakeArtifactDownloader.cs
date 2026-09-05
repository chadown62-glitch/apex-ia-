using ApexSetup.Core.Download;

namespace ApexSetup.Core.Tests.Fixtures;

/// <summary>
/// Double de test de <see cref="IArtifactDownloader"/> — isole la logique
/// D'ORCHESTRATION de <c>DownloadStage</c> (répartition entre plusieurs
/// artefacts, tentatives bornées, remplissage du contexte, rollback) du
/// comportement réseau réel, déjà testé exhaustivement dans
/// <c>Download/HttpArtifactDownloaderTests.cs</c>.
/// </summary>
public sealed class FakeArtifactDownloader : IArtifactDownloader
{
    private readonly Dictionary<string, Queue<ArtifactDownloadResult>> _resultsByUrl = new();
    public List<string> RequestedUrls { get; } = [];

    public FakeArtifactDownloader Enqueue(string url, params ArtifactDownloadResult[] results)
    {
        if (!_resultsByUrl.TryGetValue(url, out var queue))
        {
            queue = new Queue<ArtifactDownloadResult>();
            _resultsByUrl[url] = queue;
        }
        foreach (var r in results) queue.Enqueue(r);
        return this;
    }

    public Task<ArtifactDownloadResult> DownloadAsync(ArtifactDownloadRequest request, CancellationToken cancellationToken = default)
    {
        var url = request.SourceUrl.ToString();
        RequestedUrls.Add(url);
        if (_resultsByUrl.TryGetValue(url, out var queue) && queue.Count > 0)
        {
            var result = queue.Dequeue();
            if (result.IsSuccess)
            {
                // Matérialise réellement le fichier attendu, comme le ferait un vrai téléchargement.
                Directory.CreateDirectory(Path.GetDirectoryName(request.DestinationPath)!);
                File.WriteAllText(request.DestinationPath, "contenu factice vérifié par le double, pas par un vrai hash");
            }
            return Task.FromResult(result);
        }
        return Task.FromResult(ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "aucune réponse configurée", null));
    }
}
