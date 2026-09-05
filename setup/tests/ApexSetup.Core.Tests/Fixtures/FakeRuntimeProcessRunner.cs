using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Core.Tests.Fixtures;

/// <summary>
/// Double de test de <see cref="IRuntimeProcessRunner"/> — isole la
/// logique de <c>RuntimeAdapter</c> (classification d'erreur, parsing,
/// policy de version) de l'exécution réelle d'un processus. Ce n'est PAS
/// un hack pour « faire passer » les tests : l'implémentation réelle
/// (<c>RuntimeProcessRunner</c>) est testée séparément, exclusivement
/// contre de vrais sous-processus, dans
/// <see cref="RuntimeAdapter.RuntimeProcessRunnerTests"/>.
/// </summary>
public sealed class FakeRuntimeProcessRunner : IRuntimeProcessRunner
{
    private readonly ProcessInvocationResult _result;
    public IReadOnlyList<string>? CapturedArguments { get; private set; }

    public FakeRuntimeProcessRunner(ProcessInvocationResult result)
    {
        _result = result;
    }

    public static FakeRuntimeProcessRunner Success(string stdout, int exitCode = 0) => new(new ProcessInvocationResult
    {
        ProcessStarted = true,
        TimedOut = false,
        ExitCode = exitCode,
        Stdout = stdout,
        Stderr = string.Empty,
    });

    public static FakeRuntimeProcessRunner NotFound(string message = "exécutable introuvable") => new(new ProcessInvocationResult
    {
        ProcessStarted = false,
        TimedOut = false,
        StartFailureMessage = message,
    });

    public static FakeRuntimeProcessRunner TimedOut() => new(new ProcessInvocationResult
    {
        ProcessStarted = true,
        TimedOut = true,
    });

    public static FakeRuntimeProcessRunner NonZeroExit(string stdout, string stderr, int exitCode) => new(new ProcessInvocationResult
    {
        ProcessStarted = true,
        TimedOut = false,
        ExitCode = exitCode,
        Stdout = stdout,
        Stderr = stderr,
    });

    public Task<ProcessInvocationResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        CapturedArguments = arguments;
        return Task.FromResult(_result);
    }
}
