namespace ApexSetup.Dashboard.Services;

/// <summary>Résultat d'une exécution de processus MENÉE À TERME (voir <see cref="IProcessRunner.RunAsync"/>).</summary>
public sealed record ProcessRunResult
{
    public required bool Started { get; init; }
    public bool TimedOut { get; init; }
    public int? ExitCode { get; init; }
    public string Stdout { get; init; } = string.Empty;
    public string Stderr { get; init; } = string.Empty;
    public string? FailureReason { get; init; }

    public static ProcessRunResult NotStarted(string reason) => new() { Started = false, FailureReason = reason };
}

/// <summary>
/// Exécution de processus — TOUJOURS courte et bornée dans le temps (le Panel ne lance jamais de processus de longue durée), via une liste d'arguments typés
/// (<c>ArgumentList</c> côté implémentation réelle), JAMAIS une chaîne
/// shell assemblée par concaténation : même discipline que
/// <c>ApexSetup.Launcher.ProcessLaunch.RealProcessLauncher</c> et
/// <c>ApexSetup.Core.RuntimeAdapter.RuntimeProcessRunner</c> (voir
/// <c>RealProcessLauncherSecurityTests</c> côté Launcher pour la preuve
/// déjà établie que ce patron résiste à un argument hostile).
/// </summary>
public interface IProcessRunner
{
    /// <summary>Lance un processus et ATTEND sa fin (borné par <paramref name="timeout"/>) — pour les commandes courtes (sondes, lectures).</summary>
    Task<ProcessRunResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default);
}
