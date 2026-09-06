using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class PlanViewModelTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-gui-plan-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-gui-plan-dest-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        Directory.Delete(_destination, recursive: true);
    }

    [Fact]
    public async Task Never_modifies_the_machine_and_allows_Continue_only_on_a_real_success()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession { ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest };
        var vm = new PlanViewModel(service, session, new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.True(vm.Result!.Success);
        Assert.NotNull(vm.Result.Plan);
        Assert.Same(vm.Result, session.LastPlan);
        Assert.True(vm.ContinueCommand.CanExecute(null));
        Assert.False(Directory.Exists(Path.Combine(_destination, "artifacts")));
    }

    [Fact]
    public async Task Blocks_Continue_on_an_unreadable_manifest_never_a_crash()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = Path.Combine(_manifestDir, "missing.json"),
        };
        var vm = new PlanViewModel(service, session, new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.False(vm.Result!.Success);
        Assert.Null(vm.Result.Plan);
        Assert.NotNull(vm.ErrorMessage);
        Assert.False(vm.ContinueCommand.CanExecute(null));
    }
}
