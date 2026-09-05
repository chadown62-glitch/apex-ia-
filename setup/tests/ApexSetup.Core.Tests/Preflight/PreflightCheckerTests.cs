using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.Preflight;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Preflight;

public class PreflightCheckerTests
{
    private static ApexSetup.Core.RuntimeAdapter.RuntimeAdapter MakeAdapter(RoutingFakeRuntimeProcessRunner runner) =>
        new(runner, "apex", []);

    private static RoutingFakeRuntimeProcessRunner HealthyRunner() => new RoutingFakeRuntimeProcessRunner()
        .WithResponse("version", SampleJsonPayloads.ValidVersion)
        .WithResponse("doctor", SampleJsonPayloads.ValidDoctorWithSchema, exitCode: 1)
        .WithResponse("gate", SampleJsonPayloads.ValidGateFail, exitCode: 1);

    private static ModelManifestEntry Model(string id) => new()
    {
        ModelId = id, Version = "1", Quantization = "q4_k_m", ServedModel = id,
        SizeBytes = 1000, Sha256 = new string('a', 64), RequiredVramGb = 1, RequiredRamGb = 1,
        MaxContext = 8192, CapabilityClass = "coder", SupportedProfiles = [HardwareProfileTier.Standard], Source = "test",
    };

    private static InstallationPlan HealthyPlan(string destination) => new()
    {
        TargetApexVersion = "0.1.2",
        SelectedProfile = HardwareProfileTier.Standard,
        RequiredArtifacts = [],
        RequiredModels =
        [
            Model(HardwareProfileCatalog.ModelIdCoderStandard),
            Model(HardwareProfileCatalog.ModelIdJudgePrimary),
            Model(HardwareProfileCatalog.ModelIdFast),
            Model(HardwareProfileCatalog.ModelIdEmbed),
        ],
        Destination = destination,
        RequiredDiskBytes = 100,
        Steps = [],
        Dependencies = new Dictionary<string, DependencyRequirement> { ["docker"] = DependencyRequirement.Required },
    };

    [Fact]
    public async Task RunAsync_passes_every_check_for_a_fully_healthy_scenario()
    {
        var tempDir = Directory.CreateTempSubdirectory("apex-setup-preflight-").FullName;
        try
        {
            var report = await PreflightChecker.RunAsync(
                MakeAdapter(HealthyRunner()), HealthyPlan(tempDir),
                new FakeEnvironmentProbe(availableDiskBytes: 1_000_000, canWrite: true),
                minimumApexVersion: "0.1.0");

            Assert.False(report.HasBlockingFailure, string.Join("; ", report.Results.Where(r => !r.Passed).Select(r => r.Detail)));
            Assert.True(report.AllPassed);
            Assert.Equal(11, report.Results.Count);
        }
        finally
        {
            Directory.Delete(tempDir, recursive: true);
        }
    }

    [Fact]
    public async Task RunAsync_blocks_when_the_runtime_binary_is_absent()
    {
        var runner = new RoutingFakeRuntimeProcessRunner(); // aucune réponse configurée
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(runner), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, true));

        Assert.True(report.HasBlockingFailure);
        Assert.False(report.Results.Single(r => r.Name == "runtime.available").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_the_runtime_version_is_below_the_minimum()
    {
        var runner = HealthyRunner();
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(runner), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, true),
            minimumApexVersion: "99.0.0");

        Assert.True(report.HasBlockingFailure);
        Assert.False(report.Results.Single(r => r.Name == "runtime.version_compatible").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_doctor_is_inexploitable()
    {
        var runner = HealthyRunner().WithResponse("doctor", "ceci n'est pas du JSON");
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(runner), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, true));

        Assert.False(report.Results.Single(r => r.Name == "runtime.doctor_exploitable").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_gate_is_inexploitable()
    {
        var runner = HealthyRunner().WithResponse("gate", "{}"); // JSON valide mais champs obligatoires absents
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(runner), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, true));

        Assert.False(report.Results.Single(r => r.Name == "runtime.gate_exploitable").Passed);
        // Le manque d'exploitabilité de Gate rend aussi les dépendances "inconnues".
        Assert.False(report.Results.Single(r => r.Name == "dependencies.known").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_on_insufficient_disk_space()
    {
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(HealthyRunner()), HealthyPlan("/tmp"), new FakeEnvironmentProbe(availableDiskBytes: 1, canWrite: true));

        Assert.False(report.Results.Single(r => r.Name == "disk.sufficient_space").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_the_destination_is_not_writable()
    {
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(HealthyRunner()), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, canWrite: false));

        Assert.False(report.Results.Single(r => r.Name == "fs.write_permission").Passed);
    }

    [Theory]
    [InlineData("")]
    [InlineData("relative/path")]
    [InlineData("/")]
    public async Task RunAsync_blocks_on_an_invalid_destination(string destination)
    {
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(HealthyRunner()),
            HealthyPlan(string.IsNullOrEmpty(destination) ? "" : destination),
            new FakeEnvironmentProbe(1_000_000, true));

        Assert.False(report.Results.Single(r => r.Name == "destination.valid").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_a_required_model_is_missing_from_the_manifest()
    {
        var incompletePlan = HealthyPlan("/tmp") with { RequiredModels = [Model(HardwareProfileCatalog.ModelIdFast)] };
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(HealthyRunner()), incompletePlan, new FakeEnvironmentProbe(1_000_000, true));

        Assert.False(report.Results.Single(r => r.Name == "models.required_known").Passed);
    }

    [Fact]
    public async Task RunAsync_blocks_when_a_gate_missing_dependency_is_not_classified_in_the_plan()
    {
        var planWithoutDependencyClassification = HealthyPlan("/tmp") with
        {
            Dependencies = new Dictionary<string, DependencyRequirement>(),
        };
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(HealthyRunner()), planWithoutDependencyClassification, new FakeEnvironmentProbe(1_000_000, true));

        Assert.False(report.Results.Single(r => r.Name == "dependencies.known").Passed);
    }

    [Fact]
    public async Task RunAsync_runs_every_check_even_after_an_early_one_fails_never_stopping_short()
    {
        var runner = new RoutingFakeRuntimeProcessRunner(); // tout échoue dès "runtime.available"
        var report = await PreflightChecker.RunAsync(
            MakeAdapter(runner), HealthyPlan("/tmp"), new FakeEnvironmentProbe(1_000_000, true));

        // Le rapport doit être COMPLET (11 résultats), pas interrompu à la première panne.
        Assert.Equal(11, report.Results.Count);
    }
}
