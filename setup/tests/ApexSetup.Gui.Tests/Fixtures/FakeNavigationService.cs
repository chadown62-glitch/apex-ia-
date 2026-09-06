using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.Tests.Fixtures;

internal sealed class FakeNavigationService : INavigationService
{
    public WizardStep CurrentStep { get; private set; } = WizardStep.Welcome;
    public List<WizardStep> NavigationHistory { get; } = [];
    public event EventHandler? CurrentStepChanged;

    public void NavigateTo(WizardStep step)
    {
        CurrentStep = step;
        NavigationHistory.Add(step);
        CurrentStepChanged?.Invoke(this, EventArgs.Empty);
    }
}
