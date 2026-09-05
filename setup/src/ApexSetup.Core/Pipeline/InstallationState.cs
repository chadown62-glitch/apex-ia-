using System.Text.Json;

namespace ApexSetup.Core.Pipeline;

/// <summary>
/// TROUVÉ EN REVUE ADVERSARIALE (§14 Audit de l'état transactionnel) :
/// AUCUN état n'était persisté entre deux exécutions du processus avant
/// cette classe — <c>InstallationContext</c> est purement en mémoire.
/// Un processus tué en cours de pipeline ne laissait donc AUCUNE trace
/// exploitable au redémarrage : impossible de distinguer une installation
/// interrompue d'un répertoire jamais touché, impossible de savoir quelles
/// étapes avaient réussi. Voir <see cref="RecoveryClassifier"/> et
/// <see cref="InstallationRecovery"/>.
/// </summary>
public enum InstallationLifecyclePhase
{
    InProgress,
    Committed,
}

public sealed record InstallationState
{
    public required string PlanFingerprint { get; init; }
    public required IReadOnlyList<string> RequiredArtifactIds { get; init; }
    public required IReadOnlyList<string> CompletedStageNames { get; init; }
    public required InstallationLifecyclePhase Phase { get; init; }
    public required string UpdatedAtUtc { get; init; }
}

/// <summary>
/// Persistance ATOMIQUE de <see cref="InstallationState"/> — même patron
/// que le reste du moteur (temp + <c>File.Move(overwrite:true)</c>) :
/// aucun lecteur ne peut jamais observer un fichier à moitié écrit, un
/// crash pendant l'écriture laisse soit l'ancien fichier intact, soit le
/// nouveau complet, jamais un mélange.
/// </summary>
public static class InstallationStateStore
{
    public const string FileName = ".apex-setup-state.json";

    public static async Task WriteAsync(string destinationDirectory, InstallationState state, CancellationToken cancellationToken = default)
    {
        Directory.CreateDirectory(destinationDirectory);
        var path = Path.Combine(destinationDirectory, FileName);
        var tempPath = path + ".writing";
        var json = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
        await File.WriteAllTextAsync(tempPath, json, cancellationToken).ConfigureAwait(false);
        File.Move(tempPath, path, overwrite: true);
    }

    /// <summary>
    /// Retourne null si le fichier est absent OU illisible/corrompu —
    /// jamais une exception qui remonterait : c'est à
    /// <see cref="RecoveryClassifier"/> de décider ce qu'un état absent
    /// signifie (rien à faire) par opposition à un état corrompu (déjà
    /// distingué via <see cref="File.Exists(string?)"/> par l'appelant).
    /// </summary>
    public static InstallationState? TryRead(string destinationDirectory)
    {
        var path = Path.Combine(destinationDirectory, FileName);
        if (!File.Exists(path))
        {
            return null;
        }
        try
        {
            return JsonSerializer.Deserialize<InstallationState>(File.ReadAllText(path));
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public static void Delete(string destinationDirectory)
    {
        var path = Path.Combine(destinationDirectory, FileName);
        if (File.Exists(path))
        {
            File.Delete(path);
        }
    }
}
