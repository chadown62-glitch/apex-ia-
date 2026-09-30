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
/// Racine du Panel. Observateur : il mesure (<see cref="PanelStatusService"/>) et affiche ; il ne
/// démarre ni WSL ni le backend. Mesure au démarrage puis toutes les <see cref="RefreshInterval"/>
/// secondes (un backend démarré ou arrêté après l'ouverture du Panel est vu sans action), jamais
/// deux mesures en même temps. Chaque changement d'état est journalisé.
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
        Action<Action>? uiDispatcher = null)
    {
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
        ShowDiagnosticCommand = new RelayCommand(() =>
        {
            DiagnosticText = BuildDiagnostic();
            CopyMessage = null;
            IsDiagnosticVisible = true;
        });
        HideDiagnosticCommand = new RelayCommand(() => IsDiagnosticVisible = false);
        CopyDiagnosticCommand = new AsyncRelayCommand(_ => CopyDiagnosticAsync());
        OpenSettingsCommand = new RelayCommand(() => IsSettingsVisible = true);
        CloseSettingsCommand = new RelayCommand(() => IsSettingsVisible = false);
        foreach (var command in new[] { RefreshCommand, CopyDiagnosticCommand })
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

    public AsyncRelayCommand RefreshCommand { get; }
    public RelayCommand ShowDiagnosticCommand { get; }
    public RelayCommand HideDiagnosticCommand { get; }
    public AsyncRelayCommand CopyDiagnosticCommand { get; }
    public RelayCommand OpenSettingsCommand { get; }
    public RelayCommand CloseSettingsCommand { get; }

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
        var settingsViewModel = new SettingsViewModel(_settingsStore, _settings);
        settingsViewModel.Saved += (_, saved) =>
        {
            _settings = saved;
            _settingsProblem = null;
            IsSettingsVisible = false;
            _log.Log($"Paramètres enregistrés (distribution WSL : {saved.WslDistro ?? "par défaut"}).");
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
                snapshot = await _statusService.MeasureAsync(_settings).ConfigureAwait(false);
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
            });
        }
        finally
        {
            _ui(() => IsChecking = false);
            _measuring.Release();
        }
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
