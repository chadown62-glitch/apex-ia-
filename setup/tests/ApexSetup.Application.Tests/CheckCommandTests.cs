using ApexSetup.Application.Requests;
using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Contracts;
using Xunit;

namespace ApexSetup.Application.Tests;

public class CheckCommandTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-setup-check-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-check-dest-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        Directory.Delete(_destination, recursive: true);
    }

    private SetupPlanRequest MakeRequest(string manifestPath) => new()
    {
        TargetApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifestPath, ProfileOverride = HardwareProfileTier.Minimal,
    };

    [Fact]
    public async Task CheckAsync_passes_and_never_triggers_an_installation_when_everything_is_healthy()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.CheckAsync(MakeRequest(manifest));

        Assert.True(response.Success);
        Assert.False(response.HasBlockingFailure);
        Assert.NotEmpty(response.Checks);
        Assert.All(response.Checks, c => Assert.True(c.Passed));
        // Aucune installation déclenchée : ni marqueur de commit, ni artefact.
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
        Assert.False(Directory.Exists(Path.Combine(_destination, "artifacts")));
    }

    [Fact]
    public async Task CheckAsync_reports_runtime_unavailable_as_a_blocking_check_never_as_a_crash()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("version").WithNotFound("doctor").WithNotFound("gate");
        var service = ServiceFactory.Build(runner);

        var response = await service.CheckAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.True(response.HasBlockingFailure);
        Assert.Contains(response.Checks, c => c.Name == "runtime.available" && !c.Passed);
    }

    [Fact]
    public async Task CheckAsync_reports_gate_blocking_distinctly_from_a_generic_preflight_failure()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorPass)
            .WithResponse("gate", SamplePayloads.GateFailBlocking);
        var service = ServiceFactory.Build(runner);

        var response = await service.CheckAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        var gateCheck = Assert.Single(response.Checks, c => c.Name == "gate.phase2_authorized");
        Assert.False(gateCheck.Passed);
        Assert.True(gateCheck.Blocking);
    }

    [Fact]
    public async Task CheckAsync_fails_cleanly_with_a_structured_error_on_an_unreadable_manifest()
    {
        var request = MakeRequest(Path.Combine(_manifestDir, "does-not-exist.json"));
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.CheckAsync(request);

        Assert.False(response.Success);
        Assert.Empty(response.Checks); // jamais un rapport partiel construit sur un plan invalide
        Assert.Single(response.Errors);
        Assert.Equal("manifest_load_failed", response.Errors[0].Code);
    }

    [Fact]
    public async Task CheckAsync_fails_cleanly_when_a_model_needed_by_the_profile_has_no_source_url()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir, sourceUrlOverride: null);
        // Réécrit le manifeste SANS source_url pour le modèle "fast" — reproduit directement l'absence du champ.
        var withoutUrl = """
            [
              {"model_id":"qwen2-5-coder-7b-instruct","version":"1","quantization":"q4_k_m","served_model":"x",
               "size_bytes":12,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
               "required_vram_gb":5,"required_ram_gb":8,"max_context":32768,"capability_class":"coder",
               "supported_profiles":["MINIMAL"],"source":"test"},
              {"model_id":"bge-m3-latest","version":"1","quantization":"q8_0","served_model":"x",
               "size_bytes":6,"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
               "required_vram_gb":1,"required_ram_gb":2,"max_context":8192,"capability_class":"embedding",
               "supported_profiles":["MINIMAL"],"source":"test","source_url":"https://models.invalid/embed"}
            ]
            """;
        File.WriteAllText(manifest, withoutUrl);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.CheckAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.Equal("missing_source_url", response.Errors[0].Code);
    }

    [Fact]
    public async Task CheckAsync_run_twice_in_a_row_produces_the_same_verdict()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var first = await service.CheckAsync(MakeRequest(manifest));
        var second = await service.CheckAsync(MakeRequest(manifest));

        Assert.Equal(first.Success, second.Success);
        Assert.Equal(first.HasBlockingFailure, second.HasBlockingFailure);
        Assert.Equal(first.Checks.Count, second.Checks.Count);
    }
}
