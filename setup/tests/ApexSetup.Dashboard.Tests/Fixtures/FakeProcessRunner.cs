using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.Tests.Fixtures;

/// <summary>
/// Double de test pour <see cref="IProcessRunner"/> — route par une
/// correspondance sur le PREMIER argument significatif (après <c>-d
/// &lt;distro&gt;</c> et <c>--</c>) pour rester lisible dans les tests,
/// jamais une correspondance sur la chaîne complète (fragile au moindre
/// changement d'ordre d'arguments).
/// </summary>
public sealed class FakeProcessRunner : IProcessRunner
{
    private readonly Dictionary<string, Func<ProcessRunResult>> _runResponses = new();
    private readonly List<(string Executable, IReadOnlyList<string> Arguments)> _runInvocations = [];
    private readonly List<FakeTrackedProcess> _startedProcesses = [];
    private readonly List<(string Executable, IReadOnlyList<string> Arguments)> _startInvocations = [];
    public bool NextStartFails { get; set; }

    public IReadOnlyList<(string Executable, IReadOnlyList<string> Arguments)> RunInvocations => _runInvocations;
    public IReadOnlyList<FakeTrackedProcess> StartedProcesses => _startedProcesses;
    public IReadOnlyList<(string Executable, IReadOnlyList<string> Arguments)> StartInvocations => _startInvocations;

    public FakeProcessRunner WithRunResponse(string keyword, ProcessRunResult result)
    {
        _runResponses[keyword] = () => result;
        return this;
    }

    public FakeProcessRunner WithRunResponse(string keyword, Func<ProcessRunResult> resultFactory)
    {
        _runResponses[keyword] = resultFactory;
        return this;
    }

    public Task<ProcessRunResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        _runInvocations.Add((executable, arguments));
        var joined = string.Join(' ', arguments);
        foreach (var (keyword, factory) in _runResponses)
        {
            if (joined.Contains(keyword, StringComparison.Ordinal))
            {
                return Task.FromResult(factory());
            }
        }
        return Task.FromResult(ProcessRunResult.NotStarted($"aucune réponse configurée pour : {joined}"));
    }

    public ITrackedProcess? Start(string executable, IReadOnlyList<string> arguments)
    {
        _startInvocations.Add((executable, arguments));
        if (NextStartFails)
        {
            return null;
        }
        var process = new FakeTrackedProcess();
        _startedProcesses.Add(process);
        return process;
    }
}

/// <summary>Processus suivi factice — permet à un test de simuler un crash (<see cref="Exit"/>) sans jamais lancer de vrai sous-processus.</summary>
public sealed class FakeTrackedProcess : ITrackedProcess
{
    public bool HasExited { get; private set; }
    public int? ExitCode { get; private set; }
    public bool Killed { get; private set; }

    public void Exit(int exitCode)
    {
        HasExited = true;
        ExitCode = exitCode;
    }

    public void Kill()
    {
        Killed = true;
        HasExited = true;
    }

    public Task WaitForExitAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}
