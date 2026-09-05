namespace ApexSetup.Core.RuntimeAdapter;

/// <summary>Résultat brut d'une invocation de processus — aucune interprétation, uniquement des faits observés.</summary>
public sealed record ProcessInvocationResult
{
    /// <summary>False si le processus n'a jamais pu démarrer (exécutable introuvable, permission refusée, etc.).</summary>
    public required bool ProcessStarted { get; init; }

    /// <summary>True si le délai imparti a expiré avant la fin du processus (le processus a alors été tué).</summary>
    public required bool TimedOut { get; init; }

    public int? ExitCode { get; init; }
    public string Stdout { get; init; } = string.Empty;
    public string Stderr { get; init; } = string.Empty;
    public string? StartFailureMessage { get; init; }
}

/// <summary>
/// Abstraction UNIQUE d'exécution de sous-processus — jamais un import
/// direct du Runtime, jamais une interprétation du texte humain produit.
/// Une seule implémentation réelle (<see cref="RuntimeProcessRunner"/>,
/// <c>System.Diagnostics.Process</c>) ; une implémentation de test
/// (<c>FakeRuntimeProcessRunner</c>, projet de tests) pour isoler la
/// logique de l'adaptateur de l'exécution réelle de processus — la
/// couche <see cref="RuntimeProcessRunner"/> elle-même est testée
/// séparément contre de VRAIS sous-processus (voir
/// <c>RuntimeProcessRunnerTests</c>).
/// </summary>
public interface IRuntimeProcessRunner
{
    Task<ProcessInvocationResult> RunAsync(
        string executable,
        IReadOnlyList<string> arguments,
        TimeSpan timeout,
        CancellationToken cancellationToken = default);
}
