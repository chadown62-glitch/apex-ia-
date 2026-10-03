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
    private LaunchAttempt? _launch;
    private bool _stopping;
    private string? _actionMessage;
    private string _overlayTitle = "Diagnostic";

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
        PanelControl? control = null)
    {
        _control = control;
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
        OpenUiCommand = new AsyncRelayCommand(_ => OpenUiAsync(), () => CanOpen);
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
        foreach (var command in new[] { RefreshCommand, CopyDiagnosticCommand, StartCommand, StopCommand, OpenUiCommand, ShowLaunchLogCommand })
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
        private set => SetProperty(ref _isSettingsVisible, value);
    }

    public bool IsDiagnosticVisible
    {
        get => _isDiagnosticVisible;
        private set => SetProperty(ref _isDiagnosticVisible, value);
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
    public AsyncRelayCommand ShowLaunchLogCommand { get; }

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
        _log.Log($"Démarrage d'APEX demandé — dossier « {root} » ({source}) : launch.sh {BackendLauncher.StartCommand} "
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
        _log.Log($"launch.sh s'est terminé (code {code}).");
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
            _log.Log($"Arrêt d'APEX demandé — dossier « {root} » : launch.sh {BackendLauncher.StopCommand}.");
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

    private async Task OpenUiAsync()
    {
        var access = _statusService.LastAccess;
        if (_control is null || access is null)
        {
            return;
        }
        var launch = await _control.Client.CreateLaunchCodeAsync(access.Address, access.Port, access.Token).ConfigureAwait(false);
        string message;
        if (!launch.Ok || launch.Url is null)
        {
            message = $"Ouverture impossible : {launch.Detail}";
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

    private async Task ShowLaunchLogAsync()
    {
        var read = await _statusService.ReadLaunchLogAsync(_settings).ConfigureAwait(false);
        var text = read.Kind switch
        {
            FileReadKind.Ok => _redactor.Redact(LaunchLogExcerpt(read.Content ?? "")),
            FileReadKind.NotFound => "Aucun journal de démarrage : APEX n'a pas encore été démarré depuis le Panel (~/" + BackendLauncher.LaunchLog + ").",
            _ => $"Journal illisible : {read.Detail}",
        };
        _ui(() =>
        {
            OverlayTitle = "Journal du démarrage (~/" + BackendLauncher.LaunchLog + ")";
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
        OnPropertyChanged(nameof(CanStart));
        OnPropertyChanged(nameof(CanStop));
        OnPropertyChanged(nameof(CanOpen));
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
