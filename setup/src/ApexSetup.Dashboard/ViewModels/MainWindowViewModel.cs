using System.Collections.ObjectModel;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Mvvm;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.ViewModels;

/// <summary>
/// Racine du Dashboard — mission §4/§5/§12/§13/§14/§15. Chaque ligne de
/// <see cref="ComponentStates"/> vient d'une mesure réelle (<see cref="RefreshAsync"/>),
/// jamais fabriquée depuis l'état des boutons.
/// </summary>
public sealed class MainWindowViewModel : ObservableObject
{
    private readonly ISettingsStore _settingsStore;
    private readonly IWslProbe _wslProbe;
    private readonly IApexOrchestrator _orchestrator;
    private readonly IRuntimeStatusService _runtimeStatusService;
    private readonly IApiHealthChecker _apiHealthChecker;
    private readonly IWebPanelChecker _webPanelChecker;
    private readonly ILogSink _logSink;

    private DashboardSettings _settings = new();
    private bool _isSettingsVisible;
    private bool _isBusy;
    private string _statusHeadline = "Vérification initiale en cours…";

    public MainWindowViewModel(
        ISettingsStore settingsStore, IWslProbe wslProbe, IApexOrchestrator orchestrator,
        IRuntimeStatusService runtimeStatusService, IApiHealthChecker apiHealthChecker,
        IWebPanelChecker webPanelChecker, ILogSink logSink)
    {
        _settingsStore = settingsStore;
        _wslProbe = wslProbe;
        _orchestrator = orchestrator;
        _runtimeStatusService = runtimeStatusService;
        _apiHealthChecker = apiHealthChecker;
        _webPanelChecker = webPanelChecker;
        _logSink = logSink;
        _logSink.EntryAdded += (_, _) => LogLines.Add(_logSink.Entries[^1]);

        // BUG RÉEL trouvé par revue de code (finalisation APEX.exe 1/2) :
        // chacune des 4 commandes de cycle de vie ne désactivait QUE
        // elle-même pendant son exécution (IsRunning individuel) — rien
        // n'empêchait de cliquer « ARRÊTER » pendant que « LANCER » tournait
        // encore, deux opérations incompatibles lancées en parallèle sur le
        // même ApexOrchestrator (course sur _backend/_frontend). `IsBusy`
        // est désormais partagé par les 4 (canExecute + XAML), exactement
        // l'exigence explicite de la mission (§3 : « Même logique pour STOP
        // RESTART REPAIR »).
        LaunchCommand = new AsyncRelayCommand(_ => RunActionAsync(s => _orchestrator.StartAsync(s)), () => !IsBusy);
        StopCommand = new AsyncRelayCommand(_ => RunActionAsync(s => _orchestrator.StopAsync(s)), () => !IsBusy);
        RestartCommand = new AsyncRelayCommand(_ => RunActionAsync(s => _orchestrator.RestartAsync(s)), () => !IsBusy);
        RepairCommand = new AsyncRelayCommand(_ => RunActionAsync(s => _orchestrator.RepairAsync(s)), () => !IsBusy);
        RefreshCommand = new AsyncRelayCommand(_ => RefreshAsync());
        OpenSettingsCommand = new RelayCommand(() => IsSettingsVisible = true);
        CloseSettingsCommand = new RelayCommand(() => IsSettingsVisible = false);

        // BUG RÉEL (finalisation APEX.exe 1/2) : AsyncRelayCommand.Faulted
        // n'existait pas avant cette mission — une exception inattendue
        // dans Start/Stop/Restart/Repair aurait fermé APEX.exe sans aucune
        // trace (async void, voir Mvvm/RelayCommand.cs). Elle est désormais
        // interceptée et rendue visible dans le journal, jamais silencieuse
        // ni fatale — voir mission §4.
        foreach (var command in new[] { LaunchCommand, StopCommand, RestartCommand, RepairCommand, RefreshCommand })
        {
            command.Faulted += (_, ex) => _logSink.Log($"Erreur inattendue : {ex.Message}");
        }
    }

    /// <summary>Une opération de cycle de vie (Start/Stop/Restart/Repair) est en cours — voir docstring du constructeur.</summary>
    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (SetProperty(ref _isBusy, value))
            {
                LaunchCommand.NotifyCanExecuteChanged();
                StopCommand.NotifyCanExecuteChanged();
                RestartCommand.NotifyCanExecuteChanged();
                RepairCommand.NotifyCanExecuteChanged();
            }
        }
    }

    public ObservableCollection<ComponentState> ComponentStates { get; } = [];

    public ObservableCollection<LogEntry> LogLines { get; } = [];

    public SettingsViewModel? Settings { get; private set; }

    public bool IsSettingsVisible
    {
        get => _isSettingsVisible;
        private set => SetProperty(ref _isSettingsVisible, value);
    }

    public string StatusHeadline
    {
        get => _statusHeadline;
        private set => SetProperty(ref _statusHeadline, value);
    }

    public AsyncRelayCommand LaunchCommand { get; }
    public AsyncRelayCommand StopCommand { get; }
    public AsyncRelayCommand RestartCommand { get; }
    public AsyncRelayCommand RepairCommand { get; }
    public AsyncRelayCommand RefreshCommand { get; }
    public RelayCommand OpenSettingsCommand { get; }
    public RelayCommand CloseSettingsCommand { get; }

    /// <summary>Chargement initial — appelé explicitement par la racine de composition (jamais dans le constructeur : garde les tests synchrones et déterministes).</summary>
    public async Task InitializeAsync()
    {
        _settings = await _settingsStore.LoadAsync().ConfigureAwait(false);
        Settings = new SettingsViewModel(_settingsStore, _wslProbe, _settings);
        Settings.Saved += (_, saved) =>
        {
            _settings = saved;
            IsSettingsVisible = false;
            _ = RefreshAsync();
        };
        Settings.CloseRequested += (_, _) => IsSettingsVisible = false;
        OnPropertyChanged(nameof(Settings));
        await RefreshAsync().ConfigureAwait(false);
    }

    /// <summary>Relit l'état réel de chaque composant — WSL, puis Docker/PostgreSQL/Redis/MinIO/Ollama/GPU (apex doctor), puis API, puis Web Panel. Jamais déduit de l'état des boutons.</summary>
    public async Task RefreshAsync()
    {
        var states = new List<ComponentState>
        {
            await _wslProbe.ProbeAsync(_settings.WslDistro).ConfigureAwait(false),
        };
        states.AddRange(await _runtimeStatusService.GetComponentStatesAsync().ConfigureAwait(false));
        states.Add(await _apiHealthChecker.CheckAsync(_settings).ConfigureAwait(false));
        states.Add(await _webPanelChecker.CheckAsync(_settings).ConfigureAwait(false));

        ComponentStates.Clear();
        foreach (var state in states)
        {
            ComponentStates.Add(state);
        }

        StatusHeadline = states.Count(s => s.Status == ComponentStatus.Running) == states.Count
            ? "APEX est prêt."
            : $"{states.Count(s => s.Status == ComponentStatus.Running)}/{states.Count} composants en fonctionnement.";
    }

    private async Task RunActionAsync(Func<DashboardSettings, Task<OrchestratorOutcome>> action)
    {
        IsBusy = true;
        try
        {
            await action(_settings).ConfigureAwait(false);
            await RefreshAsync().ConfigureAwait(false);
        }
        finally
        {
            IsBusy = false;
        }
    }
}
