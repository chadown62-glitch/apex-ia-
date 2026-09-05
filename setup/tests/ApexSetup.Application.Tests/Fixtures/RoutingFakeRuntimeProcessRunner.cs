using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Application.Tests.Fixtures;

/// <summary>Double de test qui distingue la réponse par sous-commande (version/doctor/gate) — même patron que côté ApexSetup.Core.Tests, dupliqué ici pour garder ce projet de tests indépendant.</summary>
internal sealed class RoutingFakeRuntimeProcessRunner : IRuntimeProcessRunner
{
    private readonly Dictionary<string, ProcessInvocationResult> _byCommand = new();
    public List<IReadOnlyList<string>> Invocations { get; } = [];

    public RoutingFakeRuntimeProcessRunner WithResponse(string command, string stdout, int exitCode = 0)
    {
        _byCommand[command] = new ProcessInvocationResult
        {
            ProcessStarted = true, TimedOut = false, ExitCode = exitCode, Stdout = stdout, Stderr = string.Empty,
        };
        return this;
    }

    public RoutingFakeRuntimeProcessRunner WithNotFound(string command)
    {
        _byCommand[command] = new ProcessInvocationResult { ProcessStarted = false, TimedOut = false, StartFailureMessage = "introuvable" };
        return this;
    }

    public Task<ProcessInvocationResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        Invocations.Add(arguments);
        var command = arguments.Count > 0 ? arguments[0] : string.Empty;
        if (_byCommand.TryGetValue(command, out var result))
        {
            return Task.FromResult(result);
        }
        return Task.FromResult(new ProcessInvocationResult
        {
            ProcessStarted = false, TimedOut = false, StartFailureMessage = $"aucune réponse configurée pour « {command} »",
        });
    }
}
