using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Pipeline;
using Xunit;

namespace ApexSetup.Application.Tests;

public class RecoverCommandTests : IDisposable
{
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-recover-dest-").FullName;

    public void Dispose() => Directory.Delete(_destination, recursive: true);

    [Fact]
    public async Task RecoverAsync_reports_NoInstallation_and_takes_no_action_on_an_untouched_destination()
    {
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.RecoverAsync(_destination);

        Assert.True(response.Success);
        Assert.Equal("no_installation", response.StateDetected);
        Assert.Equal("none", response.ActionTaken);
    }

    [Fact]
    public async Task RecoverAsync_reports_Completed_and_takes_no_action_on_a_finished_installation()
    {
        await File.WriteAllTextAsync(Path.Combine(_destination, ".apex-setup-installed"), "{}");
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.RecoverAsync(_destination);

        Assert.True(response.Success);
        Assert.Equal("completed", response.StateDetected);
        Assert.Equal("none", response.ActionTaken);
    }

    [Fact]
    public async Task RecoverAsync_actually_restores_the_original_file_after_an_interrupted_installation_never_silently()
    {
        // Reproduit un « crash » : A préexistant, sauvegardé puis écrasé par
        // l'installation, jamais commité — état laissé exactement comme
        // InstallationStage/InstallationPipeline le feraient sur un vrai kill -9.
        var artifactsDir = Path.Combine(_destination, "artifacts");
        Directory.CreateDirectory(artifactsDir);
        var original = "ORIGINAL"u8.ToArray();
        var backupPath = Path.Combine(artifactsDir, "model-a.installation-backup");
        await File.WriteAllBytesAsync(backupPath, original);
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "model-a"), "NOUVELLE-VERSION"u8.ToArray());

        await InstallationStateStore.WriteAsync(_destination, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = ["model-a"], CompletedStageNames = ["download", "installation"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.RecoverAsync(_destination);

        Assert.True(response.Success);
        Assert.Equal("recovery_needed", response.StateDetected);
        Assert.Equal("rollback_performed", response.ActionTaken);
        Assert.Equal(original, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "model-a")));
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-state.json")));
    }

    [Fact]
    public async Task RecoverAsync_never_touches_disk_and_reports_Inconsistent_on_a_corrupt_state()
    {
        await File.WriteAllTextAsync(Path.Combine(_destination, ".apex-setup-installed"), "{}");
        await InstallationStateStore.WriteAsync(_destination, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["download"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.RecoverAsync(_destination);

        Assert.False(response.Success);
        Assert.Equal("inconsistent", response.StateDetected);
        Assert.Equal("none", response.ActionTaken);
        Assert.Equal("state_inconsistent", response.Errors[0].Code);
    }

    [Fact]
    public async Task RecoverAsync_refuses_when_another_execution_currently_holds_the_lock()
    {
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());
        using var heldLock = InstallationLock.TryAcquire(_destination);
        Assert.NotNull(heldLock);

        var response = await service.RecoverAsync(_destination);

        Assert.False(response.Success);
        Assert.Equal("concurrency_refused", response.Errors[0].Code);
    }
}
