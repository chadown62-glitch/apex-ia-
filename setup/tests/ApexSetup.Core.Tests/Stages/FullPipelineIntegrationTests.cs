using System.Net;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Download;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.Integrity;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Planning;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

/// <summary>
/// Bout en bout : Preflight → Plan → Download → Integrity Verification →
/// Installation → Configuration → Validation → Commit, avec un vrai
/// serveur HTTP local, un vrai système de fichiers temporaire, et un
/// Runtime simulé (FakeRuntimeProcessRunner — le sous-processus réel est
/// déjà prouvé en Phase 1/Stages séparément). Aucune étape n'est
/// contournée.
/// </summary>
public class FullPipelineIntegrationTests : IAsyncDisposable
{
    private readonly string _workingDir = Directory.CreateTempSubdirectory("apex-setup-e2e-work-").FullName;
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-e2e-dest-").FullName;

    public async ValueTask DisposeAsync()
    {
        Directory.Delete(_workingDir, recursive: true);
        Directory.Delete(_destDir, recursive: true);
        await Task.CompletedTask;
    }

    private static ApexSetup.Core.RuntimeAdapter.RuntimeAdapter HealthyAdapter() => new(
        new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SampleJsonPayloads.ValidVersion)
            .WithResponse("doctor", SampleJsonPayloads.ValidDoctorWithSchema, exitCode: 1)
            .WithResponse("gate", SampleJsonPayloads.ValidGateFail, exitCode: 1),
        "apex", []);

    private static IInstallationStage[] BuildStages(HttpClient httpClient, ApexSetup.Core.RuntimeAdapter.RuntimeAdapter adapter) =>
    [
        new DownloadStage(new HttpArtifactDownloader(httpClient)),
        new IntegrityVerificationStage(),
        new InstallationStage(),
        new ConfigurationStage(),
        new ValidationStage(adapter),
        new CommitStage(),
    ];

    [Fact]
    public async Task Full_pipeline_succeeds_end_to_end_with_two_real_artifacts_over_real_http()
    {
        var modelA = "poids-modele-a-contenu-realiste"u8.ToArray();
        var modelB = "poids-modele-b-embedding"u8.ToArray();

        await using var server = new LocalHttpTestServer(async ctx =>
        {
            var body = ctx.Request.Url!.AbsolutePath.EndsWith("model-a") ? modelA : modelB;
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });

        var plan = new InstallationPlan
        {
            TargetApexVersion = "0.1.2",
            SelectedProfile = HardwareProfileTier.Standard,
            RequiredArtifacts =
            [
                new RequiredArtifact { ArtifactId = "model-a", SourceUrl = server.UrlFor("model-a").ToString(), SizeBytes = modelA.Length, Sha256 = Sha256Verifier.ComputeSha256(modelA) },
                new RequiredArtifact { ArtifactId = "model-b", SourceUrl = server.UrlFor("model-b").ToString(), SizeBytes = modelB.Length, Sha256 = Sha256Verifier.ComputeSha256(modelB) },
            ],
            RequiredModels = [],
            Destination = _destDir,
            RequiredDiskBytes = modelA.Length + modelB.Length,
            Steps = [],
            Dependencies = new Dictionary<string, DependencyRequirement>(),
        };

        using var httpClient = new HttpClient();
        var pipeline = new InstallationPipeline(BuildStages(httpClient, HealthyAdapter()));
        var context = new InstallationContext { Plan = plan, WorkingDirectory = _workingDir, DestinationDirectory = _destDir };

        var result = await pipeline.RunAsync(context);

        Assert.True(result.Success, $"{result.FailedStageName} : {result.FailureDetail}");
        Assert.Equal(["download", "integrity_verification", "installation", "configuration", "validation", "commit"], result.ExecutedStageNames);
        Assert.Equal(modelA, await File.ReadAllBytesAsync(Path.Combine(_destDir, "artifacts", "model-a")));
        Assert.Equal(modelB, await File.ReadAllBytesAsync(Path.Combine(_destDir, "artifacts", "model-b")));
        Assert.True(File.Exists(Path.Combine(_destDir, "apex-user-config.json")));
        Assert.True(File.Exists(Path.Combine(_destDir, ".apex-setup-installed")));
    }

    [Fact]
    public async Task Full_pipeline_rolls_back_everything_when_one_artifact_has_a_wrong_hash_leaving_no_trace()
    {
        var modelA = "poids-modele-a"u8.ToArray();
        var modelBCorrupted = "sera-livré-mais-annoncé-avec-un-mauvais-hash"u8.ToArray();

        await using var server = new LocalHttpTestServer(async ctx =>
        {
            var body = ctx.Request.Url!.AbsolutePath.EndsWith("model-a") ? modelA : modelBCorrupted;
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });

        var plan = new InstallationPlan
        {
            TargetApexVersion = "0.1.2",
            SelectedProfile = HardwareProfileTier.Standard,
            RequiredArtifacts =
            [
                new RequiredArtifact { ArtifactId = "model-a", SourceUrl = server.UrlFor("model-a").ToString(), SizeBytes = modelA.Length, Sha256 = Sha256Verifier.ComputeSha256(modelA) },
                // Hash délibérément FAUX pour model-b — le serveur livre un contenu différent de celui annoncé.
                new RequiredArtifact { ArtifactId = "model-b", SourceUrl = server.UrlFor("model-b").ToString(), SizeBytes = modelBCorrupted.Length, Sha256 = new string('0', 64) },
            ],
            RequiredModels = [],
            Destination = _destDir,
            RequiredDiskBytes = 1000,
            Steps = [],
            Dependencies = new Dictionary<string, DependencyRequirement>(),
        };

        using var httpClient = new HttpClient();
        var pipeline = new InstallationPipeline(BuildStages(httpClient, HealthyAdapter()));
        var context = new InstallationContext { Plan = plan, WorkingDirectory = _workingDir, DestinationDirectory = _destDir };

        var result = await pipeline.RunAsync(context);

        Assert.False(result.Success);
        Assert.Equal("download", result.FailedStageName);
        // Rien de la tentative ne doit subsister : ni l'artefact valide
        // téléchargé avant l'échec, ni le répertoire d'installation.
        Assert.False(File.Exists(Path.Combine(_destDir, "artifacts", "model-a")));
        Assert.False(File.Exists(Path.Combine(_destDir, "apex-user-config.json")));
        Assert.False(File.Exists(Path.Combine(_destDir, ".apex-setup-installed")));
        Assert.False(File.Exists(Path.Combine(_workingDir, "model-a")));
        Assert.False(File.Exists(Path.Combine(_workingDir, "model-a.part")));
    }
}
