using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.Tests.Fixtures;

/// <summary>
/// Double de <see cref="IProcessRunner"/> (MOCK TEST : sert à tester l'usage de <c>wsl.exe</c>, qui
/// n'existe pas ici) — répond selon un mot-clé contenu dans la ligne d'arguments et enregistre
/// chaque invocation pour vérifier ce qui a été (ou n'a PAS été) lancé.
/// </summary>
public sealed class FakeProcessRunner : IProcessRunner
{
    private readonly List<(string Keyword, Func<ProcessRunResult> Factory)> _responses = [];
    private readonly List<(string Executable, IReadOnlyList<string> Arguments)> _invocations = [];

    public IReadOnlyList<(string Executable, IReadOnlyList<string> Arguments)> RunInvocations => _invocations;

    public FakeProcessRunner WithRunResponse(string keyword, ProcessRunResult result)
    {
        _responses.Add((keyword, () => result));
        return this;
    }

    public Task<ProcessRunResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        _invocations.Add((executable, arguments));
        var joined = string.Join(' ', arguments);
        foreach (var (keyword, factory) in _responses)
        {
            if (joined.Contains(keyword, StringComparison.Ordinal))
            {
                return Task.FromResult(factory());
            }
        }
        return Task.FromResult(ProcessRunResult.NotStarted($"aucune réponse configurée pour : {joined}"));
    }

    public static ProcessRunResult Ok(string stdout) => new() { Started = true, ExitCode = 0, Stdout = stdout };

    public static ProcessRunResult Exit(int code, string stderr) => new() { Started = true, ExitCode = code, Stderr = stderr };
}
