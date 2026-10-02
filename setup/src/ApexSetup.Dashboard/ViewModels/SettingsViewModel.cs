using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Mvvm;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.ViewModels;

/// <summary>
/// Paramètres facultatifs. Le seul réglage proposé est la distribution WSL, pour les PC qui en ont
/// plusieurs ; port, chemin du projet et jeton sont découverts automatiquement et ne sont jamais
/// demandés. Le port de repli (<see cref="DashboardSettings.ApiPort"/>) éventuellement présent dans
/// le fichier est conservé tel quel.
/// </summary>
public sealed class SettingsViewModel : ObservableObject
{
    private readonly ISettingsStore _settingsStore;
    private readonly DashboardSettings _initial;
    private readonly Func<DashboardSettings>? _current;
    private string _wslDistro;
    private string _projectRoot;
    private string? _message;

    public SettingsViewModel(ISettingsStore settingsStore, DashboardSettings initial, Func<DashboardSettings>? current = null)
    {
        _settingsStore = settingsStore;
        _initial = initial;
        _current = current;
        _wslDistro = initial.WslDistro ?? string.Empty;
        _projectRoot = initial.ProjectRoot ?? string.Empty;
        SaveCommand = new AsyncRelayCommand(SaveAsync);
        CloseCommand = new RelayCommand(() => CloseRequested?.Invoke(this, EventArgs.Empty));
        SaveCommand.Faulted += (_, ex) => Message = $"Enregistrement impossible : {ex.Message}";
    }

    public string WslDistro
    {
        get => _wslDistro;
        set => SetProperty(ref _wslDistro, value);
    }

    /// <summary>Dossier APEX pour « Démarrer APEX » (vide = dernier dossier vérifié, sinon ~/apex).</summary>
    public string ProjectRoot
    {
        get => _projectRoot;
        set => SetProperty(ref _projectRoot, value);
    }

    public string? Message
    {
        get => _message;
        private set => SetProperty(ref _message, value);
    }

    public AsyncRelayCommand SaveCommand { get; }
    public RelayCommand CloseCommand { get; }

    public event EventHandler<DashboardSettings>? Saved;
    public event EventHandler? CloseRequested;

    public DashboardSettings ToSettings() =>
        (_current?.Invoke() ?? _initial) with
        {
            WslDistro = string.IsNullOrWhiteSpace(WslDistro) ? null : WslDistro.Trim(),
            ProjectRoot = string.IsNullOrWhiteSpace(ProjectRoot) ? null : ProjectRoot.Trim(),
        };

    private async Task SaveAsync(CancellationToken cancellationToken)
    {
        var distro = WslDistro.Trim();
        if (distro.Length > 64 || distro.Any(c => char.IsControl(c) || c is '"' or '\\' or '/'))
        {
            Message = "Nom de distribution invalide.";
            return;
        }
        if (!string.IsNullOrWhiteSpace(ProjectRoot) && BackendLauncher.ScriptPath(ProjectRoot) is null)
        {
            Message = "Dossier APEX invalide : chemin absolu (/home/moi/apex) ou relatif au dossier personnel (apex), sans « .. ».";
            return;
        }
        var settings = ToSettings();
        await _settingsStore.SaveAsync(settings, cancellationToken).ConfigureAwait(false);
        Message = null;
        Saved?.Invoke(this, settings);
    }
}
