using System.Windows.Input;

namespace ApexSetup.Dashboard.Mvvm;

public sealed class RelayCommand(Action execute, Func<bool>? canExecute = null) : ICommand
{
    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter) => canExecute?.Invoke() ?? true;

    public void Execute(object? parameter) => execute();

    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}

/// <summary>
/// Commande async — jamais de blocage du thread UI. Même patron
/// qu'ApexSetup.Gui.Mvvm.AsyncRelayCommand, avec UN AJOUT réel (finalisation
/// APEX.exe 1/2) : <see cref="Execute"/> est <c>async void</c> — un
/// gestionnaire d'événement, la SEULE forme que <c>ICommand</c> permette.
/// Une exception non interceptée levée depuis un <c>async void</c> n'a
/// aucun appelant en attente pour la propager proprement : elle remonte
/// directement sur le dispatcher, ce qui termine le processus (confirmé par
/// la documentation .NET sur async void). Sans le <c>catch</c> ci-dessous,
/// N'IMPORTE QUELLE exception inattendue dans Start/Stop/Restart/Repair (ou
/// Enregistrer/Vérifier côté Paramètres) aurait fermé brutalement APEX.exe
/// — exactement le « crash sur exception non gérée » que la mission de
/// finalisation interdit explicitement. <see cref="Faulted"/> permet à
/// l'appelant d'afficher l'erreur (ex. dans le journal) sans jamais la
/// laisser remonter plus haut.
/// </summary>
public sealed class AsyncRelayCommand(Func<CancellationToken, Task> execute, Func<bool>? canExecute = null) : ICommand
{
    private CancellationTokenSource? _cts;
    private bool _isRunning;

    public event EventHandler? CanExecuteChanged;

    /// <summary>Levé quand <see cref="execute"/> lève une exception — jamais silencieux, jamais fatal (voir docstring de la classe).</summary>
    public event EventHandler<Exception>? Faulted;

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
        catch (OperationCanceledException)
        {
            // Annulation volontaire (Cancel()) — jamais une erreur à signaler.
        }
        catch (Exception ex)
        {
            Faulted?.Invoke(this, ex);
        }
        finally
        {
            IsRunning = false;
            _cts.Dispose();
            _cts = null;
        }
    }

    public void Cancel() => _cts?.Cancel();

    public void NotifyCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}
