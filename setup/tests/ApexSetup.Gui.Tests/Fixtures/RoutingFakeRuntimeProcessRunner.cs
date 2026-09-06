using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Gui.Tests.Fixtures;

/// <summary>Même patron que ApexSetup.Application.Tests — dupliqué pour garder ce projet de tests indépendant.</summary>
internal sealed class RoutingFakeRuntimeProcessRunner : IRuntimeProcessRunner
{
    private readonly Dictionary<string, ProcessInvocationResult> _byCommand = new();

    public RoutingFakeRuntimeProcessRunner WithResponse(string command, string stdout, int exitCode = 0)
    {
        _byCommand[command] = new ProcessInvocationResult
        {
            ProcessStarted = true, TimedOut = false, ExitCode = exitCode, Stdout = stdout, Stderr = string.Empty,
        };
        return this;
    }

    public Task<ProcessInvocationResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
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
