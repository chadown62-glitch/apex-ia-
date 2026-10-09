using System.Collections.ObjectModel;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Mvvm;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.ViewModels;

/// <summary>Accès au presse-papiers (fourni par la fenêtre ; remplaçable en test).</summary>
public interface IClipboardService
{
    Task SetTextAsync(string text);
}

/// <summary>
/// Racine du Panel. Il mesure (<see cref="PanelStatusService"/>) et affiche ; il AGIT seulement sur un
/// geste de l'utilisateur : « Démarrer APEX » / « Arrêter APEX » exécutent le launcher officiel
/// (<see cref="BackendLauncher"/> : launch.sh, Startup Gate compris, jamais contourné), « Ouvrir APEX »
/// ouvre l'interface web avec un lien à usage unique. Rien n'est jamais démarré automatiquement.
/// Mesure au démarrage puis toutes les <see cref="RefreshInterval"/> secondes, jamais deux mesures en
/// même temps. Chaque changement d'état et chaque action est journalisé.
/// </summary>
public sealed class MainWindowViewModel : ObservableObject
{
    public static readonly TimeSpan RefreshInterval = TimeSpan.FromSeconds(5);

    private readonly ISettingsStore _settingsStore;
    private readonly PanelStatusService _statusService;
    private readonly ILogSink _log;
    private readonly Redactor _redactor;
    private readonly Func<string?, DiagnosticContext> _diagnosticContext;
    private readonly Action<Action> _ui;
    private readonly SemaphoreSlim _measuring = new(1, 1);
    private readonly PanelControl? _control;
    private readonly RuntimePreparer? _preparer;
    private LaunchAttempt? _launch;
    private bool _stopping;
    private bool _preparing;
    private bool _installingModel;
    private bool _closeConfirmed;
    private bool _isClosePromptVisible;
    private string? _actionMessage;
    private string _overlayTitle = "Diagnostic";
    private bool _embedded;
    private string? _embeddedProblem;
    private bool _assistantWanted;
    private bool _isAssistantPage;
    private string? _assistantUrl;
    private int _assistantPid;
    private bool _openingAssistant;

    private DashboardSettings _settings = new();
    private string? _settingsProblem;
    private PanelSnapshot? _snapshot;
    private string _headline = "Vérification en cours…";
    private string _guidance = string.Empty;
    private PanelVerdict? _verdict;
    private string _lastChecked = "jamais";
    private bool _isChecking;
    private bool _isSettingsVisible;
    private bool _isDiagnosticVisible;
    private string _diagnosticText = string.Empty;
    private string? _copyMessage;

    public MainWindowViewModel(
        ISettingsStore settingsStore,
        PanelStatusService statusService,
        ILogSink log,
        Redactor redactor,
        Func<string?, DiagnosticContext> diagnosticContext,
        Action<Action>? uiDispatcher = null,
        PanelControl? control = null,
        RuntimePreparer? preparer = null,
        bool embeddedAssistant = false)
    {
        _embedded = embeddedAssistant;
        _assistantWanted = embeddedAssistant;
        _isAssistantPage = embeddedAssistant;
        _control = control;
        _preparer = preparer;
        _settingsStore = settingsStore;
        _statusService = statusService;
        _log = log;
        _redactor = redactor;
        _diagnosticContext = diagnosticContext;
        _ui = uiDispatcher ?? (action => action());
        _log.EntryAdded += (_, _) =>
        {
            var entry = _log.Entries[^1];
            _ui(() => LogLines.Add(entry));
        };

        RefreshCommand = new AsyncRelayCommand(_ => RefreshAsync());
        StartCommand = new AsyncRelayCommand(_ => StartAsync(), () => CanStart);
        StopCommand = new AsyncRelayCommand(_ => StopAsync(), () => CanStop);
        OpenUiCommand = new AsyncRelayCommand(_ => OpenUiAsync(inBrowser: !_embedded), () => CanOpen);
        OpenInBrowserCommand = new AsyncRelayCommand(_ => OpenUiAsync(inBrowser: true), () => CanOpen);
        ShowAssistantCommand = new AsyncRelayCommand(_ => ShowAssistantAsync());
        ShowControlCommand = new RelayCommand(() => IsAssistantPage = false);
        ShowLaunchLogCommand = new AsyncRelayCommand(_ => ShowLaunchLogAsync());
        ShowDiagnosticCommand = new RelayCommand(() =>
        {
            OverlayTitle = "Diagnostic";
            DiagnosticText = BuildDiagnostic();
            CopyMessage = null;
            IsDiagnosticVisible = true;
        });
        HideDiagnosticCommand = new RelayCommand(() => IsDiagnosticVisible = false);
        CopyDiagnosticCommand = new AsyncRelayCommand(_ => CopyDiagnosticAsync());
        OpenSettingsCommand = new RelayCommand(() => IsSettingsVisible = true);
        CloseSettingsCommand = new RelayCommand(() => IsSettingsVisible = false);
        PrepareCommand = new AsyncRelayCommand(_ => PrepareAsync(), () => CanPrepare);
        InstallModelCommand = new AsyncRelayCommand(_ => InstallModelAsync(), () => CanInstallModel);
        StopAndQuitCommand = new AsyncRelayCommand(_ => StopAndQuitAsync());
        QuitLeavingRunningCommand = new RelayCommand(() =>
        {
            _closeConfirmed = true;
            IsClosePromptVisible = false;
            _log.Log("Panel fermé — APEX continue de tourner (« Arrêter APEX » au prochain lancement du Panel).");
            CloseWindowRequested?.Invoke(this, EventArgs.Empty);
        });
        CancelCloseCommand = new RelayCommand(() => IsClosePromptVisible = false);
        foreach (var command in new[] { RefreshCommand, CopyDiagnosticCommand, StartCommand, StopCommand, OpenUiCommand, ShowLaunchLogCommand,
                     PrepareCommand, InstallModelCommand, StopAndQuitCommand, OpenInBrowserCommand, ShowAssistantCommand })
        {
            command.Faulted += (_, ex) => _log.Log($"Erreur inattendue : {ex.Message}");
        }
    }

    /// <summary>Fournit le presse-papiers réel (la fenêtre) — nul tant que la fenêtre n'existe pas.</summary>
    public IClipboardService? Clipboard { get; set; }

    public ObservableCollection<ComponentState> Components { get; } = [];

    public ObservableCollection<LogEntry> LogLines { get; } = [];

    public SettingsViewModel? Settings { get; private set; }

    public PanelSnapshot? Snapshot => _snapshot;

    public string Headline
    {
        get => _headline;
        private set => SetProperty(ref _headline, value);
    }

    public string Guidance
    {
        get => _guidance;
        private set => SetProperty(ref _guidance, value);
    }

    /// <summary>Nul tant qu'aucune mesure n'a abouti (jamais un verdict par défaut).</summary>
    public PanelVerdict? Verdict
    {
        get => _verdict;
        private set => SetProperty(ref _verdict, value);
    }

    public string LastChecked
    {
        get => _lastChecked;
        private set => SetProperty(ref _lastChecked, value);
    }

    public bool IsChecking
    {
        get => _isChecking;
        private set => SetProperty(ref _isChecking, value);
    }

    public bool IsSettingsVisible
    {
        get => _isSettingsVisible;
        private set
        {
            if (SetProperty(ref _isSettingsVisible, value))
            {
                OnPropertyChanged(nameof(IsAssistantHostVisible));
            }
        }
    }

    public bool IsDiagnosticVisible
    {
        get => _isDiagnosticVisible;
        private set
        {
            if (SetProperty(ref _isDiagnosticVisible, value))
            {
                OnPropertyChanged(nameof(IsAssistantHostVisible));
            }
        }
    }

    public string DiagnosticText
    {
        get => _diagnosticText;
        private set => SetProperty(ref _diagnosticText, value);
    }

    public string? CopyMessage
    {
        get => _copyMessage;
        private set => SetProperty(ref _copyMessage, value);
    }

    public string PanelVersion => $"Panel {PanelInfo.Version}";

    /// <summary>Résultat de la dernière action (démarrer, arrêter, ouvrir), en clair ; <c>null</c> si aucune.</summary>
    public string? ActionMessage
    {
        get => _actionMessage;
        private set => SetProperty(ref _actionMessage, value);
    }

    public string OverlayTitle
    {
        get => _overlayTitle;
        private set => SetProperty(ref _overlayTitle, value);
    }

    /// <summary>Le démarrage demandé par ce Panel, s'il y en a un (exposé pour les tests et le diagnostic).</summary>
    public LaunchAttempt? Launch => _launch;

    /// <summary>Mode Windows natif (APEX autonome sur ce PC, sans WSL) : boutons « Préparer APEX » et « Installer le modèle ».</summary>
    public bool IsNative => _statusService.NativeRuntime is not null;

    /// <summary>Paramètres WSL (distribution, dossier) : sans objet en mode natif.</summary>
    public bool IsWslSettingsVisible => !IsNative;

    /// <summary>« Préparer APEX » : télécharge et vérifie Python et les bibliothèques d'APEX — jamais pendant qu'APEX tourne.</summary>
    public bool CanPrepare =>
        _preparer is not null && IsNative && !_preparing && !_installingModel && !_stopping && _launch is not { Running: true }
        && _snapshot is { } s && s.Verdict is not (PanelVerdict.Ready or PanelVerdict.Degraded)
        && s.Cause is not (NotReadyCause.BackendStarting or NotReadyCause.BackendUnresponsive or NotReadyCause.ApiTimeout);

    /// <summary>« Installer le modèle » : runtime prêt et modèle absent ; une seule installation à la fois.</summary>
    public bool CanInstallModel =>
        _preparer is not null && IsNative && !_preparing && !_installingModel
        && _snapshot?.Get(ComponentKey.Runtime) is { Status: ComponentStatus.Ready }
        && _statusService.NativeRuntime?.Inspect() is { State: RuntimeState.Ready, ModelInstalled: false };

    public bool IsBusyPreparing => _preparing || _installingModel;

    /// <summary>Fermeture demandée alors qu'APEX tourne (mode natif) : l'arrêter, le laisser tourner, ou annuler.</summary>
    public bool IsClosePromptVisible
    {
        get => _isClosePromptVisible;
        private set
        {
            if (SetProperty(ref _isClosePromptVisible, value))
            {
                OnPropertyChanged(nameof(IsAssistantHostVisible));
            }
        }
    }

    // -- Assistant dans la fenêtre (0.4.1) -----------------------------------------------------------------

    /// <summary>L'assistant s'affiche DANS cette fenêtre (composant WebView2 de Windows) ; faux = navigateur.</summary>
    public bool IsEmbeddedAssistant => _embedded;

    /// <summary>Page affichée : l'assistant (vrai) ou « État et contrôle » (faux).</summary>
    public bool IsAssistantPage
    {
        get => _isAssistantPage;
        private set
        {
            if (SetProperty(ref _isAssistantPage, value))
            {
                OnPropertyChanged(nameof(IsControlPage));
                OnPropertyChanged(nameof(IsAssistantHostVisible));
            }
        }
    }

    public bool IsControlPage => !_isAssistantPage;

    /// <summary>Lien d'ouverture affiché dans la fenêtre ; <c>null</c> = APEX ne répond pas, rien à afficher.</summary>
    public string? AssistantUrl
    {
        get => _assistantUrl;
        private set
        {
            if (SetProperty(ref _assistantUrl, value))
            {
                OnPropertyChanged(nameof(IsAssistantHostVisible));
                OnPropertyChanged(nameof(AssistantMessage));
            }
        }
    }

    /// <summary>
    /// La fenêtre native de l'assistant est visible : page de l'assistant, interface ouverte, aucun calque (une
    /// fenêtre native passe au-dessus de tout ce qu'Avalonia dessine : elle masquerait le calque).
    /// </summary>
    public bool IsAssistantHostVisible =>
        _embedded && _isAssistantPage && _assistantUrl is not null && !_isDiagnosticVisible && !_isClosePromptVisible
        && !_isSettingsVisible;

    /// <summary>Ce que la page de l'assistant dit quand l'interface n'est pas affichée (jamais une page vide).</summary>
    public string AssistantMessage
    {
        get
        {
            if (!_embedded)
            {
                return _embeddedProblem is { } problem
                    ? $"L'assistant ne peut pas s'afficher dans cette fenêtre : {problem}. Il s'ouvre dans votre navigateur "
                      + "(« Ouvrir dans le navigateur »). Pour l'affichage intégré, installez « Microsoft Edge WebView2 Runtime » "
                      + "depuis https://go.microsoft.com/fwlink/p/?LinkId=2124703, puis rouvrez APEX."
                    : "L'assistant s'ouvre dans votre navigateur (« Ouvrir dans le navigateur »).";
            }
            if (_assistantUrl is not null)
            {
                return string.Empty;
            }
            if (_snapshot is null)
            {
                return "Vérification de l'état d'APEX…";
            }
            if (CanOpen)
            {
                return "Ouverture de l'assistant…";
            }
            if (_launch is { Running: true } || _snapshot.Cause is NotReadyCause.BackendStarting)
            {
                return "APEX démarre… L'assistant s'affichera ici dès qu'APEX répondra.";
            }
            if (CanStart)
            {
                return "APEX n'est pas démarré. Cliquez sur « Démarrer APEX » : l'assistant s'affichera ici.";
            }
            return $"{_snapshot.Headline} — {_snapshot.Guidance}";
        }
    }

    /// <summary>« Démarrer APEX » proposé sur la page de l'assistant quand APEX ne tourne pas.</summary>
    public bool IsStartOfferedOnAssistant => _assistantUrl is null && CanStart;

    /// <summary>
    /// Le composant d'affichage intégré n'a pas pu être créé (absent, erreur) : l'assistant passe au navigateur,
    /// et la raison est affichée et journalisée.
    /// </summary>
    public void ReportEmbeddedFailure(string reason)
    {
        _ui(() =>
        {
            if (!_embedded)
            {
                return;
            }
            _embedded = false;
            _embeddedProblem = reason;
            AssistantUrl = null;
            _log.Log($"Affichage intégré de l'assistant impossible : {reason} — « Ouvrir dans le navigateur » reste disponible.");
            OnPropertyChanged(nameof(IsEmbeddedAssistant));
            OnPropertyChanged(nameof(AssistantMessage));
            OnPropertyChanged(nameof(IsAssistantHostVisible));
        });
    }

    private async Task ShowAssistantAsync()
    {
        _ui(() => IsAssistantPage = true);
        _assistantWanted = true;
        if (_embedded && _assistantUrl is null && CanOpen)
        {
            await OpenUiAsync(inBrowser: false).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// Après chaque mesure : l'interface intégrée suit APEX. Arrêté -> plus rien d'affiché (jamais une page
    /// figée) ; démarré (ou redémarré : autre processus, la session précédente n'existe plus) -> nouveau lien
    /// d'ouverture, si l'utilisateur veut l'assistant.
    /// </summary>
    private async Task SyncAssistantAsync()
    {
        if (!_embedded)
        {
            return;
        }
        var access = _statusService.LastAccess;
        if (access is null || !CanOpen)
        {
            if (_assistantUrl is not null)
            {
                _ui(() => AssistantUrl = null);
            }
            return;
        }
        if (_assistantWanted && (_assistantUrl is null || access.Pid != _assistantPid))
        {
            await OpenUiAsync(inBrowser: false).ConfigureAwait(false);
        }
    }

    /// <summary>La fenêtre doit se fermer (après le choix de l'utilisateur).</summary>
    public event EventHandler? CloseWindowRequested;

    /// <summary>
    /// Appelé quand l'utilisateur ferme la fenêtre : en mode natif, si APEX tourne, on DEMANDE (l'arrêter ou le
    /// laisser tourner) au lieu de laisser silencieusement un processus en arrière-plan. Vrai = fermeture suspendue.
    /// </summary>
    public bool InterceptClose()
    {
        if (_closeConfirmed || !IsNative || !CanStop)
        {
            return false;
        }
        IsClosePromptVisible = true;
        return true;
    }

    /// <summary>
    /// « Démarrer APEX » : seulement quand APEX ne tourne pas (non démarré, arrêté, WSL arrêté, démarrage
    /// refusé ou échoué — on relance après correction). Jamais pendant un démarrage ou un arrêt.
    /// </summary>
    public bool CanStart =>
        _control is not null && !_stopping && _launch is not { Running: true } && _snapshot is { } s
        && (s.Verdict == PanelVerdict.Blocked
            || (s.Verdict == PanelVerdict.NotReady && s.Cause is NotReadyCause.BackendNotStarted or NotReadyCause.BackendStopped
                or NotReadyCause.WslStopped or NotReadyCause.StartFailed or NotReadyCause.StartupRefused));

    /// <summary>« Arrêter APEX » : dès qu'un backend existe (en marche, en démarrage, bloqué, muet) ou qu'un démarrage est en cours.</summary>
    public bool CanStop =>
        _control is not null && !_stopping
        && (_launch is { Running: true }
            || _snapshot is { Verdict: PanelVerdict.Ready or PanelVerdict.Degraded or PanelVerdict.VersionMismatch }
            || _snapshot is { Cause: NotReadyCause.BackendStarting or NotReadyCause.BackendUnresponsive or NotReadyCause.ApiTimeout
                or NotReadyCause.ApiError or NotReadyCause.TokenRejected or NotReadyCause.LocalhostUnreachable });

    /// <summary>« Ouvrir APEX » : seulement si APEX a répondu avec son jeton à la dernière mesure.</summary>
    public bool CanOpen => _control is not null && _statusService.LastAccess is not null
        && _snapshot is { Verdict: PanelVerdict.Ready or PanelVerdict.Degraded };

    public AsyncRelayCommand RefreshCommand { get; }
    public RelayCommand ShowDiagnosticCommand { get; }
    public RelayCommand HideDiagnosticCommand { get; }
    public AsyncRelayCommand CopyDiagnosticCommand { get; }
    public RelayCommand OpenSettingsCommand { get; }
    public RelayCommand CloseSettingsCommand { get; }
    public AsyncRelayCommand StartCommand { get; }
    public AsyncRelayCommand StopCommand { get; }
    public AsyncRelayCommand OpenUiCommand { get; }
    public AsyncRelayCommand OpenInBrowserCommand { get; }
    public AsyncRelayCommand ShowAssistantCommand { get; }
    public RelayCommand ShowControlCommand { get; }
    public AsyncRelayCommand ShowLaunchLogCommand { get; }
    public AsyncRelayCommand PrepareCommand { get; }
    public AsyncRelayCommand InstallModelCommand { get; }
    public AsyncRelayCommand StopAndQuitCommand { get; }
    public RelayCommand QuitLeavingRunningCommand { get; }
    public RelayCommand CancelCloseCommand { get; }

    /// <summary>Chargement initial puis première mesure — appelé par la racine de composition (jamais dans le constructeur).</summary>
    public async Task InitializeAsync()
    {
        var loaded = await _settingsStore.LoadAsync().ConfigureAwait(false);
        _settings = loaded.Settings;
        _settingsProblem = loaded.Problem;
        _log.Log($"APEX Panel {PanelInfo.Version} (protocole {StatusEngine.SupportedProtocol}) — {PanelInfo.OperatingSystem}");
        if (_settingsProblem is not null)
        {
            _log.Log($"Paramètres : {_settingsProblem}");
        }
        var settingsViewModel = new SettingsViewModel(_settingsStore, _settings, () => _settings);
        settingsViewModel.Saved += (_, saved) =>
        {
            _settings = saved;
            _settingsProblem = null;
            IsSettingsVisible = false;
            _log.Log($"Paramètres enregistrés (distribution WSL : {saved.WslDistro ?? "par défaut"} ; dossier APEX : {saved.EffectiveProjectRoot().Path}).");
            _ = RefreshAsync();
        };
        settingsViewModel.CloseRequested += (_, _) => IsSettingsVisible = false;
        _ui(() =>
        {
            Settings = settingsViewModel;
            OnPropertyChanged(nameof(Settings));
        });
        await RefreshAsync().ConfigureAwait(false);
    }

    /// <summary>Boucle de rafraîchissement automatique jusqu'à <paramref name="cancellationToken"/>.</summary>
    public async Task RunAutoRefreshAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(RefreshInterval);
        while (await timer.WaitForNextTickAsync(cancellationToken).ConfigureAwait(false))
        {
            await RefreshAsync().ConfigureAwait(false);
        }
    }

    /// <summary>Une mesure complète ; si une mesure est déjà en cours, ne fait rien (jamais deux en parallèle).</summary>
    public async Task RefreshAsync()
    {
        if (!await _measuring.WaitAsync(0).ConfigureAwait(false))
        {
            return;
        }
        try
        {
            _ui(() => IsChecking = true);
            PanelSnapshot snapshot;
            try
            {
                snapshot = await _statusService.MeasureAsync(_settings, launch: _launch).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _log.Log($"Mesure impossible (erreur interne du Panel) : {ex.GetType().Name}: {ex.Message}");
                _ui(() =>
                {
                    Headline = StatusEngine.Headline(PanelVerdict.NotReady);
                    Guidance = "Le Panel n'a pas pu mesurer l'état d'APEX (erreur interne, voir le journal). Nouvel essai automatique.";
                    Verdict = PanelVerdict.NotReady;
                });
                return;
            }
            var previous = _snapshot;
            _snapshot = snapshot;
            if (previous is null || Changed(previous, snapshot))
            {
                _log.Log($"{snapshot.Headline} ({snapshot.Cause}) — "
                    + string.Join(", ", snapshot.Components.Select(c => $"{c.Label} {StatusEngine.Label(c.Status)}"))
                    + $" — mesuré en {snapshot.Duration.TotalMilliseconds:0} ms");
            }
            _ui(() =>
            {
                SyncComponents(snapshot.Components);
                Headline = snapshot.Headline;
                Guidance = snapshot.Guidance;
                Verdict = snapshot.Verdict;
                LastChecked = $"{snapshot.MeasuredAt:HH:mm:ss}";
                if (IsDiagnosticVisible)
                {
                    DiagnosticText = BuildDiagnostic();
                }
                OnPropertyChanged(nameof(Snapshot));
                RaiseActionsChanged();
            });
            await RememberVerifiedProjectRootAsync().ConfigureAwait(false);
            await SyncAssistantAsync().ConfigureAwait(false);
        }
        finally
        {
            _ui(() => IsChecking = false);
            _measuring.Release();
        }
    }

    // -- Actions (toujours sur un geste de l'utilisateur) ------------------------------------------------

    private async Task StartAsync()
    {
        if (_control is null || !CanStart)
        {
            return;
        }
        var (root, source) = _settings.EffectiveProjectRoot();
        var distro = string.IsNullOrWhiteSpace(_settings.WslDistro) ? null : _settings.WslDistro.Trim();
        _log.Log(IsNative
            ? "Démarrage d'APEX demandé — python -m apex.standalone serve (préflight, apex doctor et Startup Gate compris ; journal : "
                + LaunchLogLocation + ")."
            : $"Démarrage d'APEX demandé — dossier « {root} » ({source}) : launch.sh {BackendLauncher.StartCommand} "
                + "(même préflight et même Startup Gate que dans un terminal ; journal : ~/" + BackendLauncher.LaunchLog + ").");
        var (attempt, exited) = _control.Launcher.Start(distro, root, DateTimeOffset.Now);
        _launch = attempt;
        if (attempt.FailureReason is { } reason)
        {
            _log.Log($"Démarrage impossible : {reason}");
        }
        else
        {
            _ = WatchLaunchAsync(attempt, exited);
        }
        _ui(() => ActionMessage = attempt.Running ? "Démarrage demandé…" : $"Démarrage impossible : {attempt.FailureReason}");
        RaiseActionsChangedOnUi();
        await RefreshAsync().ConfigureAwait(false);
    }

    private async Task WatchLaunchAsync(LaunchAttempt attempt, Task<int> exited)
    {
        var code = await exited.ConfigureAwait(false);
        if (!ReferenceEquals(_launch, attempt))
        {
            return; // arrêt demandé entre-temps, ou nouveau démarrage : ce résultat ne décrit plus rien
        }
        _launch = attempt with { ExitCode = code };
        _log.Log(IsNative ? $"Le processus d'APEX s'est terminé (code {code})." : $"launch.sh s'est terminé (code {code}).");
        RaiseActionsChangedOnUi();
        await RefreshAsync().ConfigureAwait(false);
    }

    private async Task StopAsync()
    {
        if (_control is null || !CanStop)
        {
            return;
        }
        _stopping = true;
        _launch = null; // un arrêt volontaire n'est jamais affiché comme un échec du démarrage
        RaiseActionsChangedOnUi();
        _ui(() => ActionMessage = "Arrêt d'APEX en cours…");
        try
        {
            var access = _statusService.LastAccess;
            var root = access?.ProjectRoot ?? _settings.EffectiveProjectRoot().Path;
            var distro = access?.Distro ?? (string.IsNullOrWhiteSpace(_settings.WslDistro) ? null : _settings.WslDistro.Trim());
            _log.Log(IsNative ? "Arrêt d'APEX demandé — python -m apex.standalone stop (arrêt propre par l'API)."
                : $"Arrêt d'APEX demandé — dossier « {root} » : launch.sh {BackendLauncher.StopCommand}.");
            var result = await _control.Launcher.StopAsync(distro, root).ConfigureAwait(false);
            foreach (var line in (result.Stderr + "\n" + result.Stdout).Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).TakeLast(6))
            {
                _log.Log($"  {line}");
            }
            var message = !result.Started ? $"Arrêt impossible : {result.FailureReason}"
                : result.TimedOut ? "Arrêt sans réponse dans le délai : voir le journal."
                : result.ExitCode == 0 ? "APEX arrêté."
                : result.ExitCode == 9 ? "Le backend APEX ne s'est pas arrêté : voir le journal."
                : $"Arrêt terminé en erreur (code {result.ExitCode}) : voir le journal.";
            _log.Log(message);
            _ui(() => ActionMessage = message);
        }
        finally
        {
            _stopping = false;
            RaiseActionsChangedOnUi();
        }
        await RefreshAsync().ConfigureAwait(false);
    }

    private async Task OpenUiAsync(bool inBrowser)
    {
        var access = _statusService.LastAccess;
        if (_control is null || access is null || (!inBrowser && _openingAssistant))
        {
            return;
        }
        _openingAssistant = !inBrowser;
        try
        {
            var launch = await _control.Client.CreateLaunchCodeAsync(access.Address, access.Port, access.Token).ConfigureAwait(false);
            string message;
            if (!launch.Ok || launch.Url is null)
            {
                message = $"Ouverture impossible : {launch.Detail}";
            }
            else if (!inBrowser && _embedded)
            {
                if (!SystemBrowserLauncher.IsLocalHttp(launch.Url))
                {
                    message = "Ouverture impossible : adresse refusée (seule l'interface APEX de cette machine peut s'afficher)";
                }
                else
                {
                    _assistantPid = access.Pid;
                    _assistantWanted = true;
                    _ui(() =>
                    {
                        AssistantUrl = launch.Url;
                        IsAssistantPage = true;
                    });
                    message = launch.UiAvailable
                        ? "Assistant APEX ouvert dans cette fenêtre (lien à usage unique)."
                        : $"Interface non construite : {launch.Detail}";
                }
            }
            else if (_control.Browser.Open(launch.Url) is { } error)
            {
                message = $"Ouverture impossible : {error}";
            }
            else
            {
                message = launch.UiAvailable
                    ? "Interface APEX ouverte dans le navigateur (lien à usage unique)."
                    : $"Navigateur ouvert, mais l'interface n'est pas construite : {launch.Detail}";
            }
            _log.Log(message); // jamais l'adresse : elle porte le code d'ouverture
            _ui(() => ActionMessage = message);
        }
        finally
        {
            _openingAssistant = false;
        }
    }

    /// <summary>Où est le journal du démarrage — affiché tel quel à l'utilisateur.</summary>
    private string LaunchLogLocation => _statusService.NativeRuntime is { } native
        ? Path.Combine(native.Home, ".apex", "runtime", "launch.log")
        : "~/" + BackendLauncher.LaunchLog;

    private async Task ShowLaunchLogAsync()
    {
        var read = await _statusService.ReadLaunchLogAsync(_settings).ConfigureAwait(false);
        var text = read.Kind switch
        {
            FileReadKind.Ok => _redactor.Redact(LaunchLogExcerpt(read.Content ?? "")),
            FileReadKind.NotFound => "Aucun journal de démarrage : APEX n'a pas encore été démarré depuis le Panel (" + LaunchLogLocation + ").",
            _ => $"Journal illisible : {read.Detail}",
        };
        if (_statusService.NativeRuntime is { } native)
        {
            // Mode natif : la préparation et l'installation du modèle ont aussi leur journal — tout au même endroit.
            foreach (var (title, relative) in new[] { ("Préparation d'APEX", ".apex/runtime/prepare.log"), ("Installation du modèle", ".apex/runtime/model-install.log") })
            {
                var extra = await LocalBackendHost.ReadAsync(Path.Combine(native.Home, relative), CancellationToken.None).ConfigureAwait(false);
                if (extra.Kind == FileReadKind.Ok)
                {
                    var tail = string.Join('\n', (extra.Content ?? "").Split('\n').TakeLast(60));
                    text += $"\n\n===== {title} ({relative}) — 60 dernières lignes =====\n" + _redactor.Redact(tail);
                }
            }
        }
        _ui(() =>
        {
            OverlayTitle = "Journal du démarrage (" + LaunchLogLocation + ")";
            DiagnosticText = text;
            CopyMessage = null;
            IsDiagnosticVisible = true;
        });
    }

    /// <summary>
    /// Début ET fin du journal : le préflight et la décision du Startup Gate sont en tête ; une fois APEX
    /// lancé, ses requêtes HTTP s'ajoutent à la suite (constaté sur Windows réel, run 17 : les dernières
    /// lignes ne montraient plus que des requêtes, jamais la décision de démarrage).
    /// </summary>
    internal static string LaunchLogExcerpt(string content, int head = 150, int tail = 100)
    {
        var lines = content.Split('\n');
        return lines.Length <= head + tail
            ? content
            : string.Join('\n', lines.Take(head))
                + $"\n… ({lines.Length - head - tail} lignes omises : requêtes servies par APEX) …\n"
                + string.Join('\n', lines.TakeLast(tail));
    }

    /// <summary>Mémorise le dossier d'un backend qui a répondu AVEC le jeton lu dans ce dossier (jamais un chemin non vérifié).</summary>
    private async Task RememberVerifiedProjectRootAsync()
    {
        if (_statusService.LastAccess?.ProjectRoot is not { } verified || verified == _settings.LastProjectRoot)
        {
            return;
        }
        var updated = _settings with { LastProjectRoot = verified };
        try
        {
            await _settingsStore.SaveAsync(updated).ConfigureAwait(false);
            _settings = updated;
            _log.Log($"Dossier APEX vérifié mémorisé pour « Démarrer APEX » : {verified}");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _log.Log($"Dossier APEX non mémorisé ({ex.GetType().Name}).");
        }
    }

    private void RaiseActionsChangedOnUi() => _ui(RaiseActionsChanged);

    private void RaiseActionsChanged()
    {
        StartCommand.NotifyCanExecuteChanged();
        StopCommand.NotifyCanExecuteChanged();
        OpenUiCommand.NotifyCanExecuteChanged();
        PrepareCommand.NotifyCanExecuteChanged();
        InstallModelCommand.NotifyCanExecuteChanged();
        OnPropertyChanged(nameof(CanStart));
        OnPropertyChanged(nameof(CanStop));
        OnPropertyChanged(nameof(CanOpen));
        OnPropertyChanged(nameof(CanPrepare));
        OnPropertyChanged(nameof(CanInstallModel));
        OnPropertyChanged(nameof(IsBusyPreparing));
        OnPropertyChanged(nameof(IsNative));
        OnPropertyChanged(nameof(IsWslSettingsVisible));
        OnPropertyChanged(nameof(AssistantMessage));
        OnPropertyChanged(nameof(IsStartOfferedOnAssistant));
        OpenInBrowserCommand.NotifyCanExecuteChanged();
    }

    // -- Mode Windows natif : préparation du runtime et du modèle (toujours sur un geste de l'utilisateur) ----------

    private async Task PrepareAsync()
    {
        if (_preparer is null || !CanPrepare)
        {
            return;
        }
        _preparing = true;
        RaiseActionsChangedOnUi();
        _log.Log("Préparation d'APEX demandée (Python officiel et bibliothèques d'APEX, empreintes vérifiées).");
        try
        {
            var progress = new SynchronousProgress(message =>
            {
                _log.Log("  " + message);
                _ui(() => ActionMessage = message);
            });
            var result = await _preparer.PrepareAsync(progress).ConfigureAwait(false);
            _log.Log(result.Message);
            _ui(() => ActionMessage = result.Message);
        }
        finally
        {
            _preparing = false;
            RaiseActionsChangedOnUi();
        }
        await RefreshAsync().ConfigureAwait(false);
    }

    private async Task InstallModelAsync()
    {
        if (_preparer is null || !CanInstallModel || _statusService.NativeRuntime is not { } native)
        {
            return;
        }
        var spec = native.TryReadSpec(out _);
        _installingModel = true;
        RaiseActionsChangedOnUi();
        _log.Log($"Installation du modèle demandée : {spec?.ModelRepo} (révision {spec?.ModelRevision}, environ {spec?.ModelEstimateMb / 1000.0:0.#} Go).");
        using var watching = new CancellationTokenSource();
        var watcher = WatchModelDownloadAsync(native, spec, watching.Token);
        try
        {
            var result = await _preparer.InstallModelAsync().ConfigureAwait(false);
            var installed = spec is not null && native.InspectModel(spec).Installed;
            var message = !result.Started ? $"Installation du modèle impossible : {result.FailureReason}"
                : result.TimedOut ? "Installation du modèle interrompue (délai dépassé) : relancez-la, le téléchargement reprend où il s'était arrêté."
                : result.ExitCode == 0 && installed ? "Modèle installé. Si APEX tourne, cliquez sur « Arrêter APEX » puis « Démarrer APEX » pour l'utiliser."
                : $"Installation du modèle échouée (code {result.ExitCode}) : vérifiez la connexion Internet puis relancez-la (le téléchargement reprend). Détail dans « Journal du démarrage ».";
            _log.Log(message);
            _ui(() => ActionMessage = message);
        }
        finally
        {
            await watching.CancelAsync().ConfigureAwait(false);
            await watcher.ConfigureAwait(false);
            _installingModel = false;
            RaiseActionsChangedOnUi();
        }
        await RefreshAsync().ConfigureAwait(false);
    }

    /// <summary>Progression RÉELLE du téléchargement : la taille sur disque du dossier du modèle, toutes les 3 s.</summary>
    private async Task WatchModelDownloadAsync(NativeRuntime native, RuntimeSpec? spec, CancellationToken cancellationToken)
    {
        if (spec is null)
        {
            return;
        }
        var folder = Path.Combine(native.ModelsDirectory, spec.ModelName);
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(3), cancellationToken).ConfigureAwait(false);
                long bytes = 0;
                try
                {
                    if (Directory.Exists(folder))
                    {
                        bytes = new DirectoryInfo(folder).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length);
                    }
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    continue;
                }
                var text = $"Téléchargement du modèle : {bytes / 1e9:0.00} Go sur environ {spec.ModelEstimateMb / 1000.0:0.#} Go…";
                _ui(() => ActionMessage = text);
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async Task StopAndQuitAsync()
    {
        IsClosePromptVisible = false;
        await StopAsync().ConfigureAwait(false);
        _closeConfirmed = true;
        _ui(() => CloseWindowRequested?.Invoke(this, EventArgs.Empty));
    }

    /// <summary>Progression rapportée immédiatement (pas de contexte capturé : le Panel la renvoie lui-même vers l'interface).</summary>
    private sealed class SynchronousProgress(Action<string> report) : IProgress<string>
    {
        public void Report(string value) => report(value);
    }

    public string BuildDiagnostic() =>
        _snapshot is null
            ? "Aucune mesure encore disponible."
            : DiagnosticReport.Build(_snapshot, _diagnosticContext(_settingsProblem), _redactor);

    private async Task CopyDiagnosticAsync()
    {
        var text = BuildDiagnostic();
        if (Clipboard is null)
        {
            CopyMessage = "Presse-papiers indisponible : sélectionnez le texte et copiez-le.";
            return;
        }
        await Clipboard.SetTextAsync(text).ConfigureAwait(false);
        _ui(() => CopyMessage = "Diagnostic copié (aucun secret n'y figure).");
        _log.Log("Diagnostic copié dans le presse-papiers.");
    }

    /// <summary>
    /// Met la liste à jour EN PLACE : une ligne n'est remplacée que si son contenu a changé, et la liste
    /// n'est reconstruite que si ses lignes ne sont plus les mêmes. Avant (cycle 3), elle était vidée et
    /// reconstruite toutes les 5 s, ce qui recréait chaque ligne affichée même sans aucun changement.
    /// </summary>
    private void SyncComponents(IReadOnlyList<ComponentState> rows)
    {
        if (!Components.Select(c => c.Key).SequenceEqual(rows.Select(r => r.Key)))
        {
            Components.Clear();
            foreach (var row in rows)
            {
                Components.Add(row);
            }
            return;
        }
        for (var i = 0; i < rows.Count; i++)
        {
            if (!Equals(Components[i], rows[i]))
            {
                Components[i] = rows[i];
            }
        }
    }

    private static bool Changed(PanelSnapshot a, PanelSnapshot b) =>
        a.Verdict != b.Verdict || a.Cause != b.Cause
        || !a.Components.Select(c => (c.Key, c.Status)).SequenceEqual(b.Components.Select(c => (c.Key, c.Status)));
}
