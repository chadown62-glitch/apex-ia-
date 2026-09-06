namespace ApexSetup.Gui.Services;

/// <summary>Les 9 écrans minimum requis, dans l'ordre du parcours principal — <see cref="Recovery"/> peut être atteint hors séquence (offert dès qu'un état interrompu est détecté).</summary>
public enum WizardStep
{
    Welcome,
    Detection,
    Check,
    Profile,
    Plan,
    InstallProgress,
    Recovery,
    Validation,
    FinalReady,
}

public interface INavigationService
{
    WizardStep CurrentStep { get; }
    event EventHandler? CurrentStepChanged;
    void NavigateTo(WizardStep step);
}
