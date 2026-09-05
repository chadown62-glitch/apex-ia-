using ApexSetup.Core.Integrity;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class IntegrityVerificationStageTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-integrity-stage-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    private static InstallationContext WithDownloaded(string tempDir, ApexSetup.Core.Contracts.RequiredArtifact artifact, string path)
    {
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(tempDir, artifacts: [artifact]),
            WorkingDirectory = tempDir,
            DestinationDirectory = tempDir,
        };
        context.SetItem("downloaded_artifact_paths", new Dictionary<string, string> { [artifact.ArtifactId] = path });
        return context;
    }

    [Fact]
    public async Task ExecuteAsync_succeeds_when_the_file_at_rest_matches_the_manifest_hash()
    {
        var content = "contenu réel"u8.ToArray();
        var path = Path.Combine(_tempDir, "artifact.bin");
        await File.WriteAllBytesAsync(path, content);
        var artifact = TestPlans.ArtifactFor(content, "artifact.bin");

        var outcome = await new IntegrityVerificationStage().ExecuteAsync(WithDownloaded(_tempDir, artifact, path));

        Assert.True(outcome.Success);
    }

    [Fact]
    public async Task ExecuteAsync_fails_when_the_file_at_rest_has_been_corrupted_since_download()
    {
        var originalContent = "contenu original"u8.ToArray();
        var path = Path.Combine(_tempDir, "artifact.bin");
        await File.WriteAllBytesAsync(path, originalContent);
        var artifact = TestPlans.ArtifactFor(originalContent, "artifact.bin");

        // Corruption survenue APRÈS le téléchargement (disque défaillant, etc.)
        // — exactement ce que cette étape doit détecter, distinct de la
        // vérification faite par HttpArtifactDownloader avant promotion.
        await File.WriteAllBytesAsync(path, "contenu corrompu"u8.ToArray());

        var outcome = await new IntegrityVerificationStage().ExecuteAsync(WithDownloaded(_tempDir, artifact, path));

        Assert.False(outcome.Success);
        Assert.Contains("intégrité invalide", outcome.ErrorMessage);
    }

    [Fact]
    public async Task ExecuteAsync_fails_when_the_downloaded_file_is_missing_entirely()
    {
        var artifact = TestPlans.ArtifactFor("x"u8.ToArray(), "artifact.bin");
        var missingPath = Path.Combine(_tempDir, "does-not-exist.bin");

        var outcome = await new IntegrityVerificationStage().ExecuteAsync(WithDownloaded(_tempDir, artifact, missingPath));

        Assert.False(outcome.Success);
    }

    [Fact]
    public async Task RollbackAsync_is_a_pure_no_op()
    {
        // Étape de lecture seule — aucune trace attendue, aucune exception.
        var context = WithDownloaded(_tempDir, TestPlans.ArtifactFor("x"u8.ToArray(), "a"), Path.Combine(_tempDir, "a"));
        await new IntegrityVerificationStage().RollbackAsync(context);
    }
}
