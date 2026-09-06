using System.Security.Cryptography;

namespace ApexSetup.Release;

/// <summary>
/// Construit un <see cref="ReleaseManifest"/> à partir d'un artefact
/// RÉEL déjà sur disque — jamais un hash/une taille inventés. Déterministe
/// à l'exception explicite de <see cref="ReleaseManifest.CreatedAtUtc"/> :
/// deux appels sur le MÊME fichier produisent le même
/// <see cref="ReleaseManifest.ArtifactSha256"/>/<see cref="ReleaseManifest.ArtifactSizeBytes"/>,
/// jamais un horodatage identique (par construction, ce n'est pas
/// l'objectif).
/// </summary>
public static class ReleaseManifestBuilder
{
    public static async Task<ReleaseManifest> BuildAsync(
        string artifactPath,
        string productName,
        string setupVersion,
        string architecture,
        string? runtimeVersion = null,
        string? buildCommitSha = null,
        CancellationToken cancellationToken = default)
    {
        if (!File.Exists(artifactPath))
        {
            throw new FileNotFoundException($"artefact introuvable : « {artifactPath} »", artifactPath);
        }

        var info = new FileInfo(artifactPath);
        var sha256 = await ComputeSha256Async(artifactPath, cancellationToken).ConfigureAwait(false);

        return new ReleaseManifest
        {
            SchemaVersion = ReleaseManifest.CurrentSchemaVersion,
            ProductName = productName,
            SetupVersion = setupVersion,
            RuntimeVersion = runtimeVersion,
            BuildCommitSha = buildCommitSha,
            ArtifactFileName = info.Name,
            ArtifactSizeBytes = info.Length,
            ArtifactSha256 = sha256,
            Architecture = architecture,
            CreatedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
        };
    }

    public static async Task<string> ComputeSha256Async(string path, CancellationToken cancellationToken = default)
    {
        await using var stream = File.OpenRead(path);
        var hash = await SHA256.HashDataAsync(stream, cancellationToken).ConfigureAwait(false);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }
}
