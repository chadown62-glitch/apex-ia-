namespace ApexSetup.Core.Download;

public sealed record ArtifactDownloadRequest
{
    public required Uri SourceUrl { get; init; }

    /// <summary>Chemin FINAL visé — le téléchargement écrit d'abord dans <c>{DestinationPath}.part</c>, jamais directement ici.</summary>
    public required string DestinationPath { get; init; }

    public required string ExpectedSha256 { get; init; }
    public long? ExpectedSizeBytes { get; init; }
}

public enum ArtifactDownloadOutcome
{
    Success,
    NetworkError,
    Timeout,
    HashMismatch,
    Cancelled,
}

public sealed record ArtifactDownloadResult
{
    public required ArtifactDownloadOutcome Outcome { get; init; }
    public string? FinalPath { get; init; }
    public string? ActualSha256 { get; init; }
    public string? ErrorDetail { get; init; }

    /// <summary>
    /// Chemin du fichier <c>.part</c> encore présent après un échec
    /// RÉSUMABLE (erreur réseau/timeout) — null si l'échec n'est pas
    /// résumable (ex. incompatibilité de hash : le fichier partiel a été
    /// supprimé, une reprise ne réparerait rien).
    /// </summary>
    public string? ResumablePartialPath { get; init; }

    public bool IsSuccess => Outcome == ArtifactDownloadOutcome.Success;

    public static ArtifactDownloadResult Success(string finalPath, string actualSha256) => new()
    {
        Outcome = ArtifactDownloadOutcome.Success, FinalPath = finalPath, ActualSha256 = actualSha256,
    };

    public static ArtifactDownloadResult Failure(ArtifactDownloadOutcome outcome, string detail, string? resumablePartialPath) => new()
    {
        Outcome = outcome, ErrorDetail = detail, ResumablePartialPath = resumablePartialPath,
    };
}

/// <summary>
/// Abstraction du téléchargement d'UN artefact — une seule implémentation
/// réelle (<see cref="HttpArtifactDownloader"/>, testée contre de vrais
/// serveurs HTTP locaux, jamais un <c>HttpMessageHandler</c> simulé
/// seul — voir <c>HttpArtifactDownloaderTests</c>).
/// </summary>
public interface IArtifactDownloader
{
    Task<ArtifactDownloadResult> DownloadAsync(ArtifactDownloadRequest request, CancellationToken cancellationToken = default);
}
