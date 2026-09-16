using ApexSetup.Dashboard.Mvvm;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Régression du BUG RÉEL (finalisation APEX.exe 1/2, voir Mvvm/RelayCommand.cs) :
/// <see cref="AsyncRelayCommand.Execute"/> est <c>async void</c> (seule forme permise par
/// <c>ICommand</c>) — avant cette correction, une exception inattendue levée par le délégué
/// n'était interceptée nulle part et aurait fait planter APEX.exe (un <c>async void</c> non
/// géré n'a pas d'appelant pour observer l'exception). Preuve ici : le délégué peut lever
/// n'importe quelle exception sans que <see cref="AsyncRelayCommand.ExecuteAsync"/> ne la laisse
/// s'échapper, et sans laisser la commande bloquée en <c>IsRunning</c>.
/// </summary>
public class AsyncRelayCommandTests
{
    [Fact]
    public async Task ExecuteAsync_never_lets_an_unexpected_exception_escape()
    {
        var command = new AsyncRelayCommand(_ => throw new InvalidOperationException("échec simulé"));

        var exception = await Record.ExceptionAsync(() => command.ExecuteAsync());

        Assert.Null(exception);
    }

    [Fact]
    public async Task ExecuteAsync_raises_Faulted_with_the_real_exception_instead_of_staying_silent()
    {
        var thrown = new InvalidOperationException("échec simulé");
        var command = new AsyncRelayCommand(_ => throw thrown);
        Exception? observed = null;
        command.Faulted += (_, ex) => observed = ex;

        await command.ExecuteAsync();

        Assert.Same(thrown, observed);
    }

    [Fact]
    public async Task ExecuteAsync_resets_IsRunning_after_a_fault_so_the_command_can_run_again()
    {
        var command = new AsyncRelayCommand(_ => throw new InvalidOperationException("échec simulé"));

        await command.ExecuteAsync();

        Assert.False(command.IsRunning);
        Assert.True(command.CanExecute(null));
    }

    [Fact]
    public async Task ExecuteAsync_does_not_raise_Faulted_for_a_voluntary_cancellation()
    {
        using var started = new SemaphoreSlim(0);
        var command = new AsyncRelayCommand(async token =>
        {
            started.Release();
            await Task.Delay(Timeout.Infinite, token);
        });
        var faulted = false;
        command.Faulted += (_, _) => faulted = true;

        var run = command.ExecuteAsync();
        await started.WaitAsync();
        command.Cancel();
        await run;

        Assert.False(faulted);
        Assert.False(command.IsRunning);
    }
}
