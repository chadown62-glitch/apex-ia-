using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class DetectionViewModelTests
{
    [Fact]
    public async Task OnNavigatedToAsync_populates_a_real_detection_result_never_a_fake_one()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession { Destination = Path.GetTempPath() };
        var nav = new FakeNavigationService();
        var vm = new DetectionViewModel(service, session, nav);

        await vm.OnNavigatedToAsync();

        Assert.False(vm.IsBusy);
        Assert.NotNull(vm.Result);
        Assert.True(vm.Result!.Success);
        Assert.Same(vm.Result, session.LastDetect); // le résultat est bien déposé dans la session partagée
        Assert.True(vm.ContinueCommand.CanExecute(null));
    }

    [Fact]
    public async Task Continue_navigates_to_Check()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession { Destination = Path.GetTempPath() };
        var nav = new FakeNavigationService();
        var vm = new DetectionViewModel(service, session, nav);
        await vm.OnNavigatedToAsync();

        await vm.ContinueCommand.ExecuteAsync();

        Assert.Contains(WizardStep.Check, nav.NavigationHistory);
    }
}
