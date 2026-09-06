using ApexSetup.Application;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Racine de navigation — implémente <see cref="INavigationService"/>
/// elle-même (un seul étage de navigation, pas besoin d'un service séparé
/// injecté). Construit les 9 écrans UNE SEULE FOIS (l'état saisi par
/// l'utilisateur — destination, manifeste... — survit à un aller-retour
/// entre écrans, jamais réinitialisé silencieusement).
/// </summary>
public sealed class MainWindowViewModel : ObservableObject, INavigationService
{
    private readonly Dictionary<WizardStep, ViewModelBase> _screens;
    private WizardStep _currentStep = WizardStep.Welcome;

    public event EventHandler? CurrentStepChanged;

    public MainWindowViewModel(ISetupApplicationService service, SetupSession session)
    {
        _screens = new Dictionary<WizardStep, ViewModelBase>
        {
            [WizardStep.Welcome] = new WelcomeViewModel(service, session, this),
            [WizardStep.Detection] = new DetectionViewModel(service, session, this),
            [WizardStep.Check] = new CheckViewModel(service, session, this),
            [WizardStep.Profile] = new ProfileViewModel(service, session, this),
            [WizardStep.Plan] = new PlanViewModel(service, session, this),
            [WizardStep.InstallProgress] = new InstallProgressViewModel(service, session, this),
            [WizardStep.Recovery] = new RecoveryViewModel(service, session, this),
            [WizardStep.Validation] = new ValidationViewModel(service, session, this),
            [WizardStep.FinalReady] = new FinalReadyViewModel(service, session, this),
        };
        CurrentViewModel = _screens[_currentStep];
    }

    /// <summary>
    /// Exposé pour que la fenêtre puisse intercepter une fermeture pendant
    /// une installation réellement en cours (revue adversariale Phase 10 :
    /// fermer la fenêtre pendant qu'<see cref="InstallProgressViewModel.InstallCommand"/>
    /// tourne abandonnait la tâche en arrière-plan sans jamais signaler
    /// l'annulation au token déjà propagé jusqu'au pipeline Core — risque
    /// réel de verrou/état orphelin, sans le moindre avertissement).
    /// </summary>
    public InstallProgressViewModel InstallScreen => (InstallProgressViewModel)_screens[WizardStep.InstallProgress];

    public WizardStep CurrentStep
    {
        get => _currentStep;
        private set => SetProperty(ref _currentStep, value);
    }

    private ViewModelBase _currentViewModel = null!;
    public ViewModelBase CurrentViewModel
    {
        get => _currentViewModel;
        private set => SetProperty(ref _currentViewModel, value);
    }

    public void NavigateTo(WizardStep step)
    {
        CurrentStep = step;
        CurrentViewModel = _screens[step];
        CurrentStepChanged?.Invoke(this, EventArgs.Empty);
        // Fire-and-forget délibéré : OnNavigatedToAsync gère elle-même
        // IsBusy/ErrorMessage — jamais bloquer la navigation elle-même
        // sur un appel réseau/sous-processus.
        _ = CurrentViewModel.OnNavigatedToAsync();
    }
}
