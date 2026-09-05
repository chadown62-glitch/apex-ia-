namespace ApexSetup.Core.Pipeline;

/// <summary>Issue de l'exécution d'une étape — jamais un simple booléen, toujours un détail exploitable en cas d'échec.</summary>
public sealed record StageOutcome
{
    public required bool Success { get; init; }
    public string? ErrorMessage { get; init; }

    public static StageOutcome Ok() => new() { Success = true };
    public static StageOutcome Failed(string message) => new() { Success = false, ErrorMessage = message };
}

/// <summary>
/// Une étape du pipeline transactionnel Preflight → Plan → Download →
/// Integrity Verification → Installation → Configuration → Validation →
/// Commit. Chaque étape qui modifie un état durable doit savoir défaire
/// PRÉCISÉMENT ce qu'elle vient de faire — jamais un rollback global
/// approximatif (voir <see cref="InstallationPipeline"/>).
/// </summary>
public interface IInstallationStage
{
    string Name { get; }

    Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default);

    /// <summary>
    /// Défait CE QUE CETTE ÉTAPE a fait — appelée uniquement si
    /// <see cref="ExecuteAsync"/> a réussi ET qu'une étape ultérieure a
    /// échoué. Jamais appelée si <see cref="ExecuteAsync"/> lui-même a
    /// échoué (rien à défaire). Doit être best-effort : une exception ici
    /// ne doit jamais empêcher le rollback des étapes précédentes (voir
    /// <see cref="InstallationPipeline"/>).
    /// </summary>
    Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default);
}
