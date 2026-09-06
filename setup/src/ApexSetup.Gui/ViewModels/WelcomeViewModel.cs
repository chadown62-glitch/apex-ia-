using ApexSetup.Application;
using ApexSetup.Gui.Mvvm;
using ApexSetup.Gui.Services;

namespace ApexSetup.Gui.ViewModels;

/// <summary>Écran 1/9 — collecte les entrées nécessaires au reste du parcours (jamais une logique métier : de simples champs texte déposés dans <see cref="SetupSession"/>).</summary>
public sealed class WelcomeViewModel : ViewModelBase
{
    public WelcomeViewModel(ISetupApplicationService service, SetupSession session, INavigationService navigation)
        : base(service, session, navigation)
    {
        _apexVersion = session.ApexVersion;
        _destination = session.Destination;
        _manifestPath = session.ManifestPath;
        StartCommand = new RelayCommand(Start, () => !string.IsNullOrWhiteSpace(ApexVersion)
            && !string.IsNullOrWhiteSpace(Destination) && !string.IsNullOrWhiteSpace(ManifestPath));
    }

    private string _apexVersion;
    public string ApexVersion { get => _apexVersion; set { SetProperty(ref _apexVersion, value); RaiseCanStart(); } }

    private string _destination;
    public string Destination { get => _destination; set { SetProperty(ref _destination, value); RaiseCanStart(); } }

    private string _manifestPath;
    public string ManifestPath { get => _manifestPath; set { SetProperty(ref _manifestPath, value); RaiseCanStart(); } }

    public RelayCommand StartCommand { get; }

    private void RaiseCanStart() => StartCommand.RaiseCanExecuteChanged();

    private void Start()
    {
        Session.ApexVersion = ApexVersion;
        Session.Destination = Destination;
        Session.ManifestPath = ManifestPath;
        Session.WorkingDirectory = string.IsNullOrWhiteSpace(Session.WorkingDirectory)
            ? System.IO.Path.Combine(System.IO.Path.GetTempPath(), "apex-setup-work")
            : Session.WorkingDirectory;
        Navigation.NavigateTo(WizardStep.Detection);
    }
}
