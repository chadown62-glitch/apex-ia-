using ApexSetup.Core.Pipeline;
using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class RecoveryViewModelTests : IDisposable
{
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-gui-recovery-dest-").FullName;

    public void Dispose() => Directory.Delete(_destination, recursive: true);

    [Fact]
    public async Task Actually_restores_the_original_file_never_silently()
    {
        var artifactsDir = Path.Combine(_destination, "artifacts");
        Directory.CreateDirectory(artifactsDir);
        var original = "ORIGINAL"u8.ToArray();
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "model-a.installation-backup"), original);
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "model-a"), "NEW"u8.ToArray());
        await InstallationStateStore.WriteAsync(_destination, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = ["model-a"], CompletedStageNames = ["download", "installation"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new RecoveryViewModel(service, new SetupSession { Destination = _destination }, new FakeNavigationService());

        await vm.RecoverCommand.ExecuteAsync();

        Assert.True(vm.Result!.Success);
        Assert.Equal("rollback_performed", vm.Result.ActionTaken);
        Assert.Equal(original, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "model-a")));
    }

    [Fact]
    public async Task Reports_no_action_on_an_untouched_destination()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new RecoveryViewModel(service, new SetupSession { Destination = _destination }, new FakeNavigationService());

        await vm.RecoverCommand.ExecuteAsync();

        Assert.True(vm.Result!.Success);
        Assert.Equal("none", vm.Result.ActionTaken);
    }
}
