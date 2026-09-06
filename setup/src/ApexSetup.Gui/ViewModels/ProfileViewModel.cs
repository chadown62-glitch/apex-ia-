using ApexSetup.Application;
using ApexSetup.Core.Contracts;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>
/// Écran 4/9 — le profil recommandé (déjà calculé par <c>detect</c>) est
/// affiché avec la note explicite « recommandation, jamais une
/// autorisation ». Gate reste seul juge — voir <c>check</c> (déjà passé
/// avant cet écran) : ce choix ne PEUT PAS transformer un refus Gate en
/// autorisation, il change seulement QUEL plan sera construit à l'écran
/// suivant.
/// </summary>
public sealed class ProfileViewModel : ViewModelBase
{
    public ProfileViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        ContinueCommand = new RelayCommand(() =>
        {
            Session.ProfileOverride = SelectedProfile;
            Navigation.NavigateTo(WizardStep.Plan);
        });
    }

    public string? RecommendedProfile => Session.LastDetect?.RecommendedProfile;
    public string? ProfileNote => Session.LastDetect?.ProfileNote;

    public IReadOnlyList<HardwareProfileTier> AvailableProfiles { get; } =
        [HardwareProfileTier.Minimal, HardwareProfileTier.Standard, HardwareProfileTier.Performance, HardwareProfileTier.HighEnd];

    private HardwareProfileTier? _selectedProfile;
    public HardwareProfileTier? SelectedProfile
    {
        get => _selectedProfile;
        set => SetProperty(ref _selectedProfile, value);
    }

    public RelayCommand ContinueCommand { get; }

    public override Task OnNavigatedToAsync()
    {
        OnPropertyChanged(nameof(RecommendedProfile));
        OnPropertyChanged(nameof(ProfileNote));
        return Task.CompletedTask;
    }
}
