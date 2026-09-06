using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class MainWindowViewModelTests
{
    [Fact]
    public void Constructs_all_9_required_screens_and_starts_on_Welcome()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new MainWindowViewModel(service, new SetupSession());

        Assert.Equal(WizardStep.Welcome, vm.CurrentStep);
        Assert.IsType<WelcomeViewModel>(vm.CurrentViewModel);
    }

    [Theory]
    [InlineData(WizardStep.Welcome, typeof(WelcomeViewModel))]
    [InlineData(WizardStep.Detection, typeof(DetectionViewModel))]
    [InlineData(WizardStep.Check, typeof(CheckViewModel))]
    [InlineData(WizardStep.Profile, typeof(ProfileViewModel))]
    [InlineData(WizardStep.Plan, typeof(PlanViewModel))]
    [InlineData(WizardStep.InstallProgress, typeof(InstallProgressViewModel))]
    [InlineData(WizardStep.Recovery, typeof(RecoveryViewModel))]
    [InlineData(WizardStep.Validation, typeof(ValidationViewModel))]
    [InlineData(WizardStep.FinalReady, typeof(FinalReadyViewModel))]
    public void NavigateTo_switches_CurrentViewModel_to_the_matching_screen_for_all_9_steps(WizardStep step, Type expectedType)
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new MainWindowViewModel(service, new SetupSession());

        vm.NavigateTo(step);

        Assert.Equal(step, vm.CurrentStep);
        Assert.IsType(expectedType, vm.CurrentViewModel);
    }

    [Fact]
    public void Navigating_back_and_forth_preserves_screen_state_never_silently_resets_it()
    {
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var vm = new MainWindowViewModel(service, new SetupSession());

        vm.NavigateTo(WizardStep.Welcome);
        var welcome = (WelcomeViewModel)vm.CurrentViewModel;
        welcome.Destination = "/some/user/typed/path";

        vm.NavigateTo(WizardStep.Detection);
        vm.NavigateTo(WizardStep.Welcome);

        Assert.Same(welcome, vm.CurrentViewModel);
        Assert.Equal("/some/user/typed/path", ((WelcomeViewModel)vm.CurrentViewModel).Destination);
    }
}
