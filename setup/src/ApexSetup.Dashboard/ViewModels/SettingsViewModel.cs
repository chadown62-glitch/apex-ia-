using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Mvvm;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.ViewModels;

/// <summary>Mission §16 — jamais de secret exposé (voir <see cref="DashboardSettings"/>) ; §7 — chemin projet jamais codé en dur, vérifié avant sauvegarde.</summary>
public sealed class SettingsViewModel : ObservableObject
{
    private readonly ISettingsStore _settingsStore;
    private readonly IWslProbe _wslProbe;

    private string _wslDistro = string.Empty;
    private string _projectPathWsl = string.Empty;
    private string _apiHost = "127.0.0.1";
    private int _apiPort = 8000;
    private int _webPanelPort = 5173;
    private string _ollamaUrl = "http://127.0.0.1:11434";
    private bool _launchOnWindowsStartup;
    private string? _verificationMessage;
    private bool? _pathVerified;

    public SettingsViewModel(ISettingsStore settingsStore, IWslProbe wslProbe, DashboardSettings initial)
    {
        _settingsStore = settingsStore;
        _wslProbe = wslProbe;
        LoadFrom(initial);

        VerifyPathCommand = new AsyncRelayCommand(VerifyPathAsync, () => !string.IsNullOrWhiteSpace(ProjectPathWsl));
        SaveCommand = new AsyncRelayCommand(SaveAsync);
        UseSuggestedPathCommand = new RelayCommand(() => ProjectPathWsl = DashboardSettings.SuggestedProjectPathWsl);
        CloseCommand = new RelayCommand(() => CloseRequested?.Invoke(this, EventArgs.Empty));

        // BUG RÉEL (finalisation APEX.exe 1/2, voir Mvvm/RelayCommand.cs et
        // MainWindowViewModel) : une exception inattendue (ex. Enregistrer
        // échoue — disque plein, dossier %AppData%\Apex refusé) aurait fermé
        // APEX.exe. Réutilise VerificationMessage, déjà affiché à l'écran,
        // plutôt qu'une nouvelle propriété — aucune UI supplémentaire requise.
        VerifyPathCommand.Faulted += (_, ex) => VerificationMessage = $"Vérification impossible : {ex.Message}";
        SaveCommand.Faulted += (_, ex) => VerificationMessage = $"Enregistrement impossible : {ex.Message}";
    }

    public string SuggestedProjectPathWsl => DashboardSettings.SuggestedProjectPathWsl;

    public string WslDistro
    {
        get => _wslDistro;
        set => SetProperty(ref _wslDistro, value);
    }

    public string ProjectPathWsl
    {
        get => _projectPathWsl;
        set
        {
            if (SetProperty(ref _projectPathWsl, value))
            {
                PathVerified = null;
                VerificationMessage = null;
            }
        }
    }

    public string ApiHost
    {
        get => _apiHost;
        set => SetProperty(ref _apiHost, value);
    }

    public int ApiPort
    {
        get => _apiPort;
        set => SetProperty(ref _apiPort, value);
    }

    public int WebPanelPort
    {
        get => _webPanelPort;
        set => SetProperty(ref _webPanelPort, value);
    }

    public string OllamaUrl
    {
        get => _ollamaUrl;
        set => SetProperty(ref _ollamaUrl, value);
    }

    public bool LaunchOnWindowsStartup
    {
        get => _launchOnWindowsStartup;
        set => SetProperty(ref _launchOnWindowsStartup, value);
    }

    public string? VerificationMessage
    {
        get => _verificationMessage;
        private set => SetProperty(ref _verificationMessage, value);
    }

    /// <summary><c>null</c> = jamais vérifié depuis la dernière modification — voir mission §7, un chemin non vérifié n'est jamais traité comme valide.</summary>
    public bool? PathVerified
    {
        get => _pathVerified;
        private set => SetProperty(ref _pathVerified, value);
    }

    public AsyncRelayCommand VerifyPathCommand { get; }
    public AsyncRelayCommand SaveCommand { get; }
    public RelayCommand UseSuggestedPathCommand { get; }
    public RelayCommand CloseCommand { get; }

    public event EventHandler<DashboardSettings>? Saved;
    public event EventHandler? CloseRequested;

    private async Task VerifyPathAsync(CancellationToken cancellationToken)
    {
        var exists = await _wslProbe.PathExistsAsync(
            string.IsNullOrWhiteSpace(WslDistro) ? null : WslDistro, ProjectPathWsl, cancellationToken).ConfigureAwait(false);
        PathVerified = exists;
        VerificationMessage = exists
            ? $"Vérifié — « {ProjectPathWsl} » existe dans WSL."
            : $"Introuvable — « {ProjectPathWsl} » n'existe pas dans la distribution WSL sélectionnée.";
    }

    private async Task SaveAsync(CancellationToken cancellationToken)
    {
        var settings = ToSettings();
        await _settingsStore.SaveAsync(settings, cancellationToken).ConfigureAwait(false);
        Saved?.Invoke(this, settings);
    }

    private void LoadFrom(DashboardSettings settings)
    {
        _wslDistro = settings.WslDistro ?? string.Empty;
        _projectPathWsl = settings.ProjectPathWsl ?? string.Empty;
        _apiHost = settings.ApiHost;
        _apiPort = settings.ApiPort;
        _webPanelPort = settings.WebPanelPort;
        _ollamaUrl = settings.OllamaUrl;
        _launchOnWindowsStartup = settings.LaunchOnWindowsStartup;
    }

    public DashboardSettings ToSettings() => new()
    {
        WslDistro = string.IsNullOrWhiteSpace(WslDistro) ? null : WslDistro,
        ProjectPathWsl = string.IsNullOrWhiteSpace(ProjectPathWsl) ? null : ProjectPathWsl,
        ApiHost = ApiHost,
        ApiPort = ApiPort,
        WebPanelPort = WebPanelPort,
        OllamaUrl = OllamaUrl,
        LaunchOnWindowsStartup = LaunchOnWindowsStartup,
    };
}
