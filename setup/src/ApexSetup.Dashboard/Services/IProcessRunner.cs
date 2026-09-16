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
/// Un processus démarré en ARRIÈRE-PLAN et suivi par le Dashboard —
/// exactement ce que la mission §13 exige pour l'arrêt ciblé : « identifier
/// exactement ce qui a été démarré par le launcher afin d'éviter de tuer
/// arbitrairement des processus qui ne lui appartiennent pas ». Chaque
/// instance représente UN SEUL processus Windows que ce Dashboard a
/// lui-même lancé (typiquement <c>wsl.exe -- ...</c>) — jamais un PID
/// deviné ou recherché après coup par nom.
/// </summary>
public interface ITrackedProcess : IAsyncDisposable
{
    bool HasExited { get; }
    int? ExitCode { get; }

    /// <summary>Termine CE processus précis (et son arbre, sous Windows) — jamais un autre processus du même nom sur la machine.</summary>
    void Kill();

    Task WaitForExitAsync(CancellationToken cancellationToken = default);
}

/// <summary>
/// Exécution de processus — TOUJOURS via une liste d'arguments typés
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

    /// <summary>Lance un processus SANS attendre sa fin — pour les commandes longues suivies explicitement (<c>apex serve</c>, <c>npm run dev</c>). Retourne <c>null</c> si le processus n'a pas pu démarrer.</summary>
    ITrackedProcess? Start(string executable, IReadOnlyList<string> arguments);
}
