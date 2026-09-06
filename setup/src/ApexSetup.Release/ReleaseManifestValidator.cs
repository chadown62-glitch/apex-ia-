namespace ApexSetup.Release;

/// <summary>Classification EXHAUSTIVE et explicite — jamais un simple booléen (même discipline que <c>RuntimeCallOutcome</c> côté Core).</summary>
public enum ReleaseValidationOutcome
{
    Valid,
    ManifestUnreadable,
    UnsupportedSchemaVersion,
    ArtifactMissing,
    SizeMismatch,
    HashMismatch,
    ArchitectureMismatch,
    VersionMismatch,
    UnsafeArtifactFileName,
}

public sealed record ReleaseValidationResult
{
    public required ReleaseValidationOutcome Outcome { get; init; }
    public required string Detail { get; init; }
    public bool IsValid => Outcome == ReleaseValidationOutcome.Valid;

    public static ReleaseValidationResult Ok(string detail) => new() { Outcome = ReleaseValidationOutcome.Valid, Detail = detail };
    public static ReleaseValidationResult Fail(ReleaseValidationOutcome outcome, string detail) => new() { Outcome = outcome, Detail = detail };
}

/// <summary>
/// Vérifie qu'un manifeste correspond RÉELLEMENT à l'artefact sur disque —
/// jamais une confiance aveugle dans le contenu déclaré du manifeste.
/// Un artefact modifié après publication (même d'un seul octet) DOIT être
/// rejeté — c'est la garantie centrale de cette classe.
/// </summary>
public static class ReleaseManifestValidator
{
    public static async Task<ReleaseValidationResult> ValidateAsync(
        string manifestPath,
        string artifactDirectory,
        string expectedArchitecture,
        string expectedSetupVersion,
        CancellationToken cancellationToken = default)
    {
        var manifest = ReleaseManifestJson.TryRead(manifestPath);
        if (manifest is null)
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.ManifestUnreadable, $"manifeste « {manifestPath} » absent, illisible ou structurellement invalide");
        }

        if (manifest.SchemaVersion > ReleaseManifest.CurrentSchemaVersion)
        {
            // Même politique que RuntimeSchemaPolicy côté Core : jamais interpréter un schéma plus récent que celui connu.
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.UnsupportedSchemaVersion,
                $"schema_version {manifest.SchemaVersion} > {ReleaseManifest.CurrentSchemaVersion} connu — refus d'interpréter");
        }

        if (!string.Equals(manifest.Architecture, expectedArchitecture, StringComparison.Ordinal))
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.ArchitectureMismatch,
                $"architecture manifeste « {manifest.Architecture} » != attendue « {expectedArchitecture} »");
        }

        if (!string.Equals(manifest.SetupVersion, expectedSetupVersion, StringComparison.Ordinal))
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.VersionMismatch,
                $"setup_version manifeste « {manifest.SetupVersion} » != attendue « {expectedSetupVersion} »");
        }

        // TROUVÉ EN REVUE ADVERSARIALE PHASE 10 (§ Security, path traversal) :
        // `Path.Combine(artifactDirectory, manifest.ArtifactFileName)` NU
        // laissait un manifeste manipulé (`artifact_file_name` = «
        // ../secret/private.txt », un chemin absolu, ou un UNC) faire
        // « valider » n'importe quel fichier HORS de `artifactDirectory` —
        // reproduit : un manifeste pointant vers un fichier arbitraire dont
        // le hash/la taille sont connus se voit répondre `Valid`, exit 0.
        // Exactement le même défaut que celui déjà documenté et corrigé
        // côté Core par `Security.PathSafety.SafeCombine` (DownloadStage/
        // InstallationStage) — mais `ApexSetup.Release` ne référence
        // délibérément PAS Core (voir en-tête de ce fichier), donc la même
        // discipline est réimplémentée ici, localement, sans dépendance.
        if (!IsSafeArtifactFileName(manifest.ArtifactFileName))
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.UnsafeArtifactFileName,
                $"artifact_file_name « {manifest.ArtifactFileName} » n'est pas un nom de fichier simple — refusé avant toute lecture disque");
        }

        var artifactPath = Path.GetFullPath(Path.Combine(artifactDirectory, manifest.ArtifactFileName));
        var artifactDirectoryFull = Path.GetFullPath(artifactDirectory);
        var artifactDirectoryWithSeparator = artifactDirectoryFull.EndsWith(Path.DirectorySeparatorChar)
            ? artifactDirectoryFull : artifactDirectoryFull + Path.DirectorySeparatorChar;
        if (!artifactPath.StartsWith(artifactDirectoryWithSeparator, StringComparison.Ordinal))
        {
            // Défense en profondeur — ne devrait jamais être atteint tant
            // qu'IsSafeArtifactFileName rejette bien tout séparateur, mais
            // protège contre une régression future de cette règle (même
            // raisonnement que PathSafety.SafeCombine côté Core).
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.UnsafeArtifactFileName,
                $"le chemin résolu « {artifactPath} » sort de « {artifactDirectoryFull} »");
        }

        if (!File.Exists(artifactPath))
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.ArtifactMissing, $"artefact déclaré « {manifest.ArtifactFileName} » absent de « {artifactDirectory} »");
        }

        var actualSize = new FileInfo(artifactPath).Length;
        if (actualSize != manifest.ArtifactSizeBytes)
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.SizeMismatch,
                $"taille réelle {actualSize:N0} octets != manifeste {manifest.ArtifactSizeBytes:N0} octets — artefact modifié ou manifeste erroné");
        }

        var actualSha256 = await ReleaseManifestBuilder.ComputeSha256Async(artifactPath, cancellationToken).ConfigureAwait(false);
        if (!string.Equals(actualSha256, manifest.ArtifactSha256, StringComparison.OrdinalIgnoreCase))
        {
            return ReleaseValidationResult.Fail(
                ReleaseValidationOutcome.HashMismatch,
                $"SHA-256 réel {actualSha256} != manifeste {manifest.ArtifactSha256} — artefact modifié après publication du manifeste");
        }

        return ReleaseValidationResult.Ok($"« {manifest.ArtifactFileName} » valide : taille et SHA-256 correspondent exactement au manifeste");
    }

    /// <summary>
    /// True si <paramref name="candidate"/> est un nom de fichier SIMPLE —
    /// aucun séparateur, aucune traversée, jamais un chemin absolu ou UNC.
    /// Réimplémentation locale, volontairement minimale, du même principe
    /// que <c>Core.Security.PathSafety.IsSafeFileName</c> — cette classe ne
    /// référence pas Core (voir en-tête du fichier), donc ne peut pas
    /// réutiliser cette classe directement sans introduire la dépendance
    /// que l'architecture de <c>ApexSetup.Release</c> interdit délibérément.
    /// </summary>
    private static bool IsSafeArtifactFileName(string candidate)
    {
        if (string.IsNullOrWhiteSpace(candidate))
        {
            return false;
        }
        if (candidate is "." or "..")
        {
            return false;
        }
        if (Path.IsPathRooted(candidate))
        {
            return false;
        }
        if (candidate.StartsWith(@"\\", StringComparison.Ordinal) || candidate.StartsWith("//", StringComparison.Ordinal))
        {
            return false;
        }
        return candidate.IndexOfAny(['/', '\\']) < 0;
    }
}
