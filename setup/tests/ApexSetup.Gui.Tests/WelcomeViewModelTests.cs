using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class WelcomeViewModelTests
{
    [Fact]
    public void StartCommand_is_disabled_until_all_required_fields_are_filled()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new WelcomeViewModel(service, new SetupSession(), new FakeNavigationService());

        Assert.False(vm.StartCommand.CanExecute(null));

        vm.ApexVersion = "0.1.2";
        Assert.False(vm.StartCommand.CanExecute(null));
        vm.Destination = "/tmp/dest";
        Assert.False(vm.StartCommand.CanExecute(null));
        vm.ManifestPath = "/tmp/manifest.json";

        Assert.True(vm.StartCommand.CanExecute(null));
    }

    [Fact]
    public void Start_deposits_the_entered_values_into_the_shared_session_and_navigates_to_Detection()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession();
        var nav = new FakeNavigationService();
        var vm = new WelcomeViewModel(service, session, nav)
        {
            ApexVersion = "0.1.2", Destination = "/tmp/dest", ManifestPath = "/tmp/manifest.json",
        };

        vm.StartCommand.Execute(null);

        Assert.Equal("0.1.2", session.ApexVersion);
        Assert.Equal("/tmp/dest", session.Destination);
        Assert.Equal("/tmp/manifest.json", session.ManifestPath);
        Assert.NotEmpty(session.WorkingDirectory);
        Assert.Contains(WizardStep.Detection, nav.NavigationHistory);
    }
}
