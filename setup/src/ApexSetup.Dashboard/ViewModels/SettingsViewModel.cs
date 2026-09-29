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
    private string _wslDistro;
    private string? _message;

    public SettingsViewModel(ISettingsStore settingsStore, DashboardSettings initial)
    {
        _settingsStore = settingsStore;
        _initial = initial;
        _wslDistro = initial.WslDistro ?? string.Empty;
        SaveCommand = new AsyncRelayCommand(SaveAsync);
        CloseCommand = new RelayCommand(() => CloseRequested?.Invoke(this, EventArgs.Empty));
        SaveCommand.Faulted += (_, ex) => Message = $"Enregistrement impossible : {ex.Message}";
    }

    public string WslDistro
    {
        get => _wslDistro;
        set => SetProperty(ref _wslDistro, value);
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
        _initial with { WslDistro = string.IsNullOrWhiteSpace(WslDistro) ? null : WslDistro.Trim() };

    private async Task SaveAsync(CancellationToken cancellationToken)
    {
        var distro = WslDistro.Trim();
        if (distro.Length > 64 || distro.Any(c => char.IsControl(c) || c is '"' or '\\' or '/'))
        {
            Message = "Nom de distribution invalide.";
            return;
        }
        var settings = ToSettings();
        await _settingsStore.SaveAsync(settings, cancellationToken).ConfigureAwait(false);
        Message = null;
        Saved?.Invoke(this, settings);
    }
}
