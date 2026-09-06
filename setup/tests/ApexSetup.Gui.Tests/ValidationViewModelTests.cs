using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class ValidationViewModelTests
{
    [Fact]
    public async Task Never_reinstalls_only_queries_the_Runtime_and_reflects_a_real_success()
    {
        var runner = TestServiceFactory.HealthyRunner();
        var service = TestServiceFactory.Build(runner);
        var vm = new ValidationViewModel(service, new SetupSession(), new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.True(vm.Result!.Success);
        Assert.True(vm.ContinueCommand.CanExecute(null));
    }

    [Fact]
    public async Task Blocks_Continue_when_the_gate_measures_a_real_fail()
    {
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorPass)
            .WithResponse("gate", SamplePayloads.GateFailBlocking);
        var service = TestServiceFactory.Build(runner);
        var vm = new ValidationViewModel(service, new SetupSession(), new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.False(vm.Result!.Success);
        Assert.False(vm.ContinueCommand.CanExecute(null));
    }
}
