using ApexSetup.Core.Download;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class DownloadStageTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-download-stage-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    private InstallationContext MakeContext(params ApexSetup.Core.Contracts.RequiredArtifact[] artifacts) => new()
    {
        Plan = TestPlans.MinimalPlan(_tempDir, artifacts: artifacts),
        WorkingDirectory = _tempDir,
        DestinationDirectory = _tempDir,
    };

    [Fact]
    public async Task ExecuteAsync_downloads_every_required_artifact_and_records_their_paths()
    {
        var a1 = TestPlans.ArtifactFor("x"u8.ToArray(), "model-a");
        var a2 = TestPlans.ArtifactFor("y"u8.ToArray(), "model-b");
        var downloader = new FakeArtifactDownloader()
            .Enqueue(a1.SourceUrl, ArtifactDownloadResult.Success(Path.Combine(_tempDir, "model-a"), a1.Sha256))
            .Enqueue(a2.SourceUrl, ArtifactDownloadResult.Success(Path.Combine(_tempDir, "model-b"), a2.Sha256));
        var stage = new DownloadStage(downloader);
        var context = MakeContext(a1, a2);

        var outcome = await stage.ExecuteAsync(context);

        Assert.True(outcome.Success);
        var paths = context.GetRequiredItem<Dictionary<string, string>>("downloaded_artifact_paths");
        Assert.Equal(2, paths.Count);
        Assert.Contains("model-a", paths.Keys);
        Assert.Contains("model-b", paths.Keys);
    }

    [Fact]
    public async Task ExecuteAsync_retries_up_to_the_configured_bound_then_fails()
    {
        var artifact = TestPlans.ArtifactFor("z"u8.ToArray(), "flaky-model");
        var downloader = new FakeArtifactDownloader().Enqueue(
            artifact.SourceUrl,
            ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure 1", null),
            ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure 2", null),
            ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure 3", null));
        var stage = new DownloadStage(downloader, maxAttemptsPerArtifact: 3);
        var context = MakeContext(artifact);

        var outcome = await stage.ExecuteAsync(context);

        Assert.False(outcome.Success);
        Assert.Equal(3, downloader.RequestedUrls.Count(u => u == artifact.SourceUrl));
    }

    [Fact]
    public async Task ExecuteAsync_succeeds_after_a_transient_failure_followed_by_success_within_the_retry_bound()
    {
        var artifact = TestPlans.ArtifactFor("w"u8.ToArray(), "eventually-ok");
        var downloader = new FakeArtifactDownloader().Enqueue(
            artifact.SourceUrl,
            ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure", null),
            ArtifactDownloadResult.Success(Path.Combine(_tempDir, "eventually-ok"), artifact.Sha256));
        var stage = new DownloadStage(downloader, maxAttemptsPerArtifact: 3);

        var outcome = await stage.ExecuteAsync(MakeContext(artifact));

        Assert.True(outcome.Success);
    }

    [Fact]
    public async Task ExecuteAsync_cleans_up_artifacts_already_downloaded_in_this_run_when_a_later_one_fails()
    {
        // Deux artefacts : le premier réussit réellement (fichier écrit sur
        // disque), le second échoue après épuisement des tentatives. Le
        // pipeline n'appelle JAMAIS RollbackAsync sur une étape qui n'a pas
        // retourné Success — c'est donc à DownloadStage lui-même de ne pas
        // laisser le premier fichier orphelin.
        var a1 = TestPlans.ArtifactFor("x"u8.ToArray(), "model-a");
        var a2 = TestPlans.ArtifactFor("y"u8.ToArray(), "model-b");
        var firstFinalPath = Path.Combine(_tempDir, "model-a");
        var downloader = new FakeArtifactDownloader()
            .Enqueue(a1.SourceUrl, ArtifactDownloadResult.Success(firstFinalPath, a1.Sha256))
            .Enqueue(a2.SourceUrl,
                ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure", null),
                ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure", null),
                ArtifactDownloadResult.Failure(ArtifactDownloadOutcome.NetworkError, "coupure", null));
        var stage = new DownloadStage(downloader, maxAttemptsPerArtifact: 3);
        var context = MakeContext(a1, a2);

        var outcome = await stage.ExecuteAsync(context);

        Assert.False(outcome.Success);
        Assert.False(File.Exists(firstFinalPath), "l'artefact téléchargé avec succès avant l'échec du second ne doit pas rester orphelin");
    }

    [Fact]
    public async Task RollbackAsync_deletes_every_downloaded_file()
    {
        var artifact = TestPlans.ArtifactFor("v"u8.ToArray(), "to-remove");
        var finalPath = Path.Combine(_tempDir, "to-remove");
        var downloader = new FakeArtifactDownloader().Enqueue(artifact.SourceUrl, ArtifactDownloadResult.Success(finalPath, artifact.Sha256));
        var stage = new DownloadStage(downloader);
        var context = MakeContext(artifact);

        await stage.ExecuteAsync(context);
        Assert.True(File.Exists(finalPath));

        await stage.RollbackAsync(context);

        Assert.False(File.Exists(finalPath));
    }
}
