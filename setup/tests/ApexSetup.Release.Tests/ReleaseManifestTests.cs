using ApexSetup.Release;
using Xunit;

namespace ApexSetup.Release.Tests;

public class ReleaseManifestTests : IDisposable
{
    private readonly string _dir = Directory.CreateTempSubdirectory("apex-release-manifest-").FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private async Task<string> WriteArtifactAsync(byte[] content, string name = "apex-setup.exe")
    {
        var path = Path.Combine(_dir, name);
        await File.WriteAllBytesAsync(path, content);
        return path;
    }

    [Fact]
    public async Task BuildAsync_computes_the_real_size_and_sha256_of_the_artifact_never_invented_values()
    {
        var content = "contenu réel de l'artefact"u8.ToArray();
        var artifactPath = await WriteArtifactAsync(content);

        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");

        Assert.Equal(content.Length, manifest.ArtifactSizeBytes);
        Assert.Equal(await ReleaseManifestBuilder.ComputeSha256Async(artifactPath), manifest.ArtifactSha256);
        Assert.Equal("apex-setup.exe", manifest.ArtifactFileName);
        Assert.Null(manifest.RuntimeVersion); // jamais inventé : aucun artefact Runtime co-packagé
    }

    [Fact]
    public async Task BuildAsync_throws_a_clear_error_for_a_missing_artifact_never_a_fabricated_manifest()
    {
        await Assert.ThrowsAsync<FileNotFoundException>(() =>
            ReleaseManifestBuilder.BuildAsync(Path.Combine(_dir, "does-not-exist.exe"), "APEX Setup", "0.1.0", "win-x64"));
    }

    [Fact]
    public async Task WriteAsync_then_TryRead_round_trips_the_manifest_exactly()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");

        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);
        var read = ReleaseManifestJson.TryRead(manifestPath);

        Assert.NotNull(read);
        Assert.Equal(manifest.ArtifactSha256, read!.ArtifactSha256);
        Assert.Equal(manifest.SetupVersion, read.SetupVersion);
    }

    [Fact]
    public async Task ValidateAsync_accepts_an_untampered_artifact()
    {
        var artifactPath = await WriteArtifactAsync("contenu original"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.True(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.Valid, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_an_artifact_modified_after_the_manifest_was_written()
    {
        var artifactPath = await WriteArtifactAsync("contenu original"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        // Tampering : même TAILLE que l'original (pour isoler spécifiquement la détection par hash, pas par taille).
        await File.WriteAllBytesAsync(artifactPath, "CONTENU-MODIFIE!"u8.ToArray());
        Assert.Equal("contenu original"u8.ToArray().Length, "CONTENU-MODIFIE!"u8.ToArray().Length);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.HashMismatch, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_an_artifact_whose_size_changed()
    {
        var artifactPath = await WriteArtifactAsync("court"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        await File.WriteAllBytesAsync(artifactPath, "un contenu beaucoup plus long qu'avant"u8.ToArray());

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.SizeMismatch, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_a_missing_artifact_file()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        File.Delete(artifactPath);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.ArtifactMissing, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_a_missing_manifest_file_never_throws()
    {
        var result = await ReleaseManifestValidator.ValidateAsync(Path.Combine(_dir, "no-such-manifest.json"), _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.ManifestUnreadable, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_a_structurally_invalid_manifest_never_throws()
    {
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await File.WriteAllTextAsync(manifestPath, "{ ceci n'est pas du JSON valide !!!");

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.ManifestUnreadable, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_an_architecture_mismatch()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "linux-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.ArchitectureMismatch, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_rejects_a_setup_version_mismatch()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "9.9.9");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.VersionMismatch, result.Outcome);
    }

    [Fact]
    public async Task ValidateAsync_refuses_a_manifest_whose_schema_version_is_newer_than_known_never_guesses()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var futureManifest = manifest with { SchemaVersion = ReleaseManifest.CurrentSchemaVersion + 1 };
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, futureManifest);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.UnsupportedSchemaVersion, result.Outcome);
    }

    [Fact]
    public async Task Manifest_JSON_never_contains_a_secret_looking_key()
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");

        var json = ReleaseManifestJson.Serialize(manifest);

        Assert.DoesNotContain("token", json, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("password", json, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("secret", json, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("key", json, StringComparison.OrdinalIgnoreCase);
    }

    // -----------------------------------------------------------------
    // PHASE 10 — revue adversariale : path traversal via artifact_file_name
    // -----------------------------------------------------------------
    // Trouvé et reproduit RÉELLEMENT (apex-setup-release validate contre un
    // manifeste manipulé pointant hors du répertoire attendu répondait
    // "Valid", exit 0) avant correctif. `artifactDirectory` simule le
    // répertoire de distribution attendu ; `secretDir` un répertoire VOISIN
    // qui ne devrait jamais être atteignable depuis un manifeste.

    [Fact]
    public async Task ValidateAsync_rejects_a_manifest_whose_artifact_file_name_escapes_the_directory_via_dot_dot()
    {
        var secretDir = Directory.CreateTempSubdirectory("apex-release-secret-").FullName;
        try
        {
            var secretPath = Path.Combine(secretDir, "private.txt");
            var secretContent = "CONTENU HORS DU REPERTOIRE ATTENDU"u8.ToArray();
            await File.WriteAllBytesAsync(secretPath, secretContent);
            var secretSha256 = await ReleaseManifestBuilder.ComputeSha256Async(secretPath);

            // Manifeste manipulé : artifact_file_name traverse vers le fichier voisin,
            // avec un hash/une taille qui correspondent réellement à CE fichier —
            // exactement ce qu'un attaquant qui contrôle le manifeste ferait.
            var manifest = new ReleaseManifest
            {
                SchemaVersion = ReleaseManifest.CurrentSchemaVersion,
                ProductName = "APEX Setup",
                SetupVersion = "0.1.0",
                ArtifactFileName = "../" + Path.GetFileName(secretDir) + "/private.txt",
                ArtifactSizeBytes = secretContent.Length,
                ArtifactSha256 = secretSha256,
                Architecture = "win-x64",
                CreatedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
            };
            var manifestPath = Path.Combine(_dir, "manifest.json");
            await ReleaseManifestJson.WriteAsync(manifestPath, manifest);

            var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

            Assert.False(result.IsValid);
            Assert.Equal(ReleaseValidationOutcome.UnsafeArtifactFileName, result.Outcome);
        }
        finally
        {
            Directory.Delete(secretDir, recursive: true);
        }
    }

    [Theory]
    [InlineData("/etc/passwd")]
    [InlineData(@"C:\Windows\System32\config")]
    [InlineData(@"\\attacker-host\share\payload.exe")]
    [InlineData("subdir/artifact.exe")]
    public async Task ValidateAsync_rejects_an_absolute_or_composed_artifact_file_name(string dangerousName)
    {
        var artifactPath = await WriteArtifactAsync("x"u8.ToArray());
        var manifest = await ReleaseManifestBuilder.BuildAsync(artifactPath, "APEX Setup", "0.1.0", "win-x64");
        var tampered = manifest with { ArtifactFileName = dangerousName };
        var manifestPath = Path.Combine(_dir, "manifest.json");
        await ReleaseManifestJson.WriteAsync(manifestPath, tampered);

        var result = await ReleaseManifestValidator.ValidateAsync(manifestPath, _dir, "win-x64", "0.1.0");

        Assert.False(result.IsValid);
        Assert.Equal(ReleaseValidationOutcome.UnsafeArtifactFileName, result.Outcome);
    }
}
