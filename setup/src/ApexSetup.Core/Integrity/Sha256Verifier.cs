using System.Security.Cryptography;

namespace ApexSetup.Core.Integrity;

public enum IntegrityCheckOutcome
{
    Match,
    Mismatch,
    FileNotFound,
}

public sealed record IntegrityCheckResult
{
    public required IntegrityCheckOutcome Outcome { get; init; }
    public required string ExpectedSha256 { get; init; }
    public string? ActualSha256 { get; init; }

    public bool IsValid => Outcome == IntegrityCheckOutcome.Match;
}

/// <summary>
/// Vérification d'intégrité SHA-256 — aucune installation d'un artefact
/// dont l'intégrité est inconnue (règle verrouillée en conception,
/// section 6/G). Comparaison insensible à la casse hexadécimale, jamais
/// approximative.
/// </summary>
public static class Sha256Verifier
{
    public static async Task<IntegrityCheckResult> VerifyFileAsync(
        string filePath, string expectedSha256Hex, CancellationToken cancellationToken = default)
    {
        if (!File.Exists(filePath))
        {
            return new IntegrityCheckResult
            {
                Outcome = IntegrityCheckOutcome.FileNotFound,
                ExpectedSha256 = expectedSha256Hex,
            };
        }

        await using var stream = File.OpenRead(filePath);
        var hashBytes = await SHA256.HashDataAsync(stream, cancellationToken).ConfigureAwait(false);
        var actual = Convert.ToHexString(hashBytes).ToLowerInvariant();

        return new IntegrityCheckResult
        {
            Outcome = string.Equals(actual, expectedSha256Hex, StringComparison.OrdinalIgnoreCase)
                ? IntegrityCheckOutcome.Match
                : IntegrityCheckOutcome.Mismatch,
            ExpectedSha256 = expectedSha256Hex,
            ActualSha256 = actual,
        };
    }

    public static string ComputeSha256(byte[] content) =>
        Convert.ToHexString(SHA256.HashData(content)).ToLowerInvariant();
}
