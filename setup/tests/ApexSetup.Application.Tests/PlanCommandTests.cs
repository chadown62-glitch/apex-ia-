using System.Text.Json;
using ApexSetup.Application.Requests;
using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.Planning;
using Xunit;

namespace ApexSetup.Application.Tests;

public class PlanCommandTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-setup-plan-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-plan-dest-").FullName;

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
    public async Task PlanAsync_never_modifies_the_machine()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.PlanAsync(MakeRequest(manifest));

        Assert.True(response.Success);
        Assert.False(Directory.Exists(Path.Combine(_destination, "artifacts")));
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-state.json")));
    }

    [Fact]
    public async Task PlanAsync_same_input_produces_an_identical_plan_and_fingerprint()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var first = await service.PlanAsync(MakeRequest(manifest));
        var second = await service.PlanAsync(MakeRequest(manifest));

        Assert.Equal(first.PlanFingerprint, second.PlanFingerprint);
        Assert.Equal(
            JsonSerializer.Serialize(first.Plan),
            JsonSerializer.Serialize(second.Plan));
    }

    [Fact]
    public async Task PlanAsync_fingerprint_matches_PlanFingerprint_Compute_directly()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.PlanAsync(MakeRequest(manifest));

        Assert.Equal(PlanFingerprint.Compute(response.Plan!), response.PlanFingerprint);
    }

    [Fact]
    public async Task PlanAsync_respects_an_explicit_profile_override_over_the_detected_recommendation()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.PlanAsync(MakeRequest(manifest));

        Assert.Equal(HardwareProfileTier.Minimal, response.Plan!.SelectedProfile);
    }
}
