using System.Windows.Input;

namespace ApexSetup.Gui.Mvvm;

public sealed class RelayCommand(Action execute, Func<bool>? canExecute = null) : ICommand
{
    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter) => canExecute?.Invoke() ?? true;

    public void Execute(object? parameter) => execute();

    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}

/// <summary>
/// Commande async — JAMAIS de blocage du thread UI (§5 « aucune opération
/// longue sur le thread UI »). <see cref="IsRunning"/> désactive
/// automatiquement une ré-exécution concurrente de la MÊME commande (pas
/// de double-clic déclenchant deux installations) ; un
/// <see cref="CancellationTokenSource"/> optionnel permet une annulation
/// réelle, propagée jusqu'au <c>CancellationToken</c> déjà accepté par
/// chaque méthode de <c>ISetupApplicationService</c>.
/// </summary>
public sealed class AsyncRelayCommand(Func<CancellationToken, Task> execute, Func<bool>? canExecute = null) : ICommand
{
    private CancellationTokenSource? _cts;
    private bool _isRunning;

    public event EventHandler? CanExecuteChanged;

    public bool IsRunning
    {
        get => _isRunning;
        private set
        {
            _isRunning = value;
            CanExecuteChanged?.Invoke(this, EventArgs.Empty);
        }
    }

    public bool CanExecute(object? parameter) => !IsRunning && (canExecute?.Invoke() ?? true);

    public async void Execute(object? parameter) => await ExecuteAsync().ConfigureAwait(false);

    public async Task ExecuteAsync()
    {
        if (IsRunning)
        {
            return;
        }
        _cts = new CancellationTokenSource();
        IsRunning = true;
        try
        {
            await execute(_cts.Token).ConfigureAwait(false);
        }
        finally
        {
            IsRunning = false;
            _cts.Dispose();
            _cts = null;
        }
    }

    /// <summary>Annulation réelle de l'opération en cours — no-op si rien n'est en cours.</summary>
    public void Cancel() => _cts?.Cancel();

    /// <summary>À appeler quand une condition externe utilisée par <c>canExecute</c> a changé (ex. un résultat vient d'être peuplé).</summary>
    public void NotifyCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}
