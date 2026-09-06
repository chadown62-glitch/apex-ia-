namespace ApexSetup.Launcher.ProcessLaunch;

/// <summary>Résultat d'un lancement — jamais lancé « fire and forget » sans code de sortie observable (règle absolue « codes d'erreur déterministes »).</summary>
public sealed record ProcessLaunchResult
{
    public required bool Started { get; init; }
    public required int? ExitCode { get; init; }
    public string? FailureReason { get; init; }

    /// <summary>Rempli uniquement par <see cref="IProcessLauncher.CaptureOutputAsync"/> — toujours <c>null</c> pour <see cref="IProcessLauncher.RunAsync"/>/<see cref="IProcessLauncher.RelaunchElevated"/>.</summary>
    public string? Stdout { get; init; }
}

/// <summary>
/// Frontière testable autour de <c>Process.Start</c> — la SEULE façon dont
/// <c>ApexSetup.Launcher</c> exécute quoi que ce soit d'externe. Toute
/// implémentation DOIT utiliser <c>ProcessStartInfo.ArgumentList</c>
/// (jamais <c>Arguments</c> en chaîne concaténée, jamais
/// <c>UseShellExecute=true</c> avec une commande shell) — règle absolue
/// #10/#11 de la mission (« jamais de shell non sûr », « tout chemin/argument
/// géré de façon sûre »). Le seul usage légitime de
/// <c>UseShellExecute=true</c> dans ce projet est la ré-exécution élevée
/// (<c>Verb="runas"</c>), qui ne prend AUCUN argument shell interprété —
/// voir <see cref="RealProcessLauncher.RelaunchElevated"/>.
/// </summary>
public interface IProcessLauncher
{
    /// <summary>Lance un exécutable avec des arguments individuels sûrs (jamais de concaténation de chaîne) et attend sa fin.</summary>
    Task<ProcessLaunchResult> RunAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken);

    /// <summary>
    /// Comme <see cref="RunAsync"/>, mais capture stdout au lieu de le
    /// laisser hérité par la console — utilisé UNIQUEMENT pour lire la
    /// sortie d'un utilitaire (ex. <c>wslpath</c>), jamais pour la sortie
    /// interactive d'APEX lui-même (qui doit rester visible en direct,
    /// voir <see cref="RunAsync"/>).
    /// </summary>
    Task<ProcessLaunchResult> CaptureOutputAsync(
        string executable, IReadOnlyList<string> arguments, string workingDirectory, CancellationToken cancellationToken);

    /// <summary>Relance le launcher lui-même avec élévation (UAC) — la SEULE opération de ce type autorisée dans tout le projet.</summary>
    ProcessLaunchResult RelaunchElevated(string executablePath, IReadOnlyList<string> arguments);
}
