using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class ValidationStageTests : IDisposable
{
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-validation-stage-").FullName;

    public void Dispose() => Directory.Delete(_destDir, recursive: true);

    private static ApexSetup.Core.RuntimeAdapter.RuntimeAdapter HealthyAdapter() => new(
        new RoutingFakeRuntimeProcessRunner()
            .WithResponse("doctor", SampleJsonPayloads.ValidDoctorWithSchema, exitCode: 1)
            .WithResponse("gate", SampleJsonPayloads.ValidGateFail, exitCode: 1),
        "apex", []);

    private InstallationContext MakeContextWithInstalledArtifact(byte[] content, string artifactId)
    {
        var artifactsDir = Path.Combine(_destDir, "artifacts");
        Directory.CreateDirectory(artifactsDir);
        File.WriteAllBytes(Path.Combine(artifactsDir, artifactId), content);

        var artifact = TestPlans.ArtifactFor(content, artifactId);
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir, artifacts: [artifact]),
            WorkingDirectory = _destDir,
            DestinationDirectory = _destDir,
        };
        context.SetItem("installed_file_paths", new List<string> { Path.Combine(artifactsDir, artifactId) });
        return context;
    }

    [Fact]
    public async Task ExecuteAsync_succeeds_when_installed_artifacts_match_and_runtime_is_still_healthy()
    {
        var context = MakeContextWithInstalledArtifact("contenu"u8.ToArray(), "model-a");

        var outcome = await new ValidationStage(HealthyAdapter()).ExecuteAsync(context);

        Assert.True(outcome.Success, outcome.ErrorMessage);
    }

    [Fact]
    public async Task ExecuteAsync_fails_when_an_installed_artifact_has_the_wrong_size_on_disk()
    {
        var content = "contenu original de taille connue"u8.ToArray();
        var context = MakeContextWithInstalledArtifact(content, "model-a");
        // Corrompt la taille après coup — simule un disque défaillant post-installation.
        File.WriteAllBytes(Path.Combine(_destDir, "artifacts", "model-a"), "court"u8.ToArray());

        var outcome = await new ValidationStage(HealthyAdapter()).ExecuteAsync(context);

        Assert.False(outcome.Success);
        Assert.Contains("taille", outcome.ErrorMessage);
    }

    [Fact]
    public async Task ExecuteAsync_fails_when_an_announced_installed_file_is_actually_missing()
    {
        var context = MakeContextWithInstalledArtifact("x"u8.ToArray(), "model-a");
        File.Delete(Path.Combine(_destDir, "artifacts", "model-a"));

        var outcome = await new ValidationStage(HealthyAdapter()).ExecuteAsync(context);

        Assert.False(outcome.Success);
    }

    [Fact]
    public async Task ExecuteAsync_fails_when_the_runtime_doctor_is_no_longer_exploitable_after_installation()
    {
        var context = MakeContextWithInstalledArtifact("x"u8.ToArray(), "model-a");
        var degradedAdapter = new ApexSetup.Core.RuntimeAdapter.RuntimeAdapter(
            new RoutingFakeRuntimeProcessRunner()
                .WithResponse("doctor", "JSON invalide")
                .WithResponse("gate", SampleJsonPayloads.ValidGateFail),
            "apex", []);

        var outcome = await new ValidationStage(degradedAdapter).ExecuteAsync(context);

        Assert.False(outcome.Success);
        Assert.Contains("Doctor", outcome.ErrorMessage);
    }

    [Fact]
    public async Task RollbackAsync_is_a_pure_no_op()
    {
        var context = MakeContextWithInstalledArtifact("x"u8.ToArray(), "model-a");
        await new ValidationStage(HealthyAdapter()).RollbackAsync(context);
    }
}
