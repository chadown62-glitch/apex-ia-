using System.Text.Json;
using ApexSetup.Core.Pipeline;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 6, POINT DE NON-RETOUR : écrit le marqueur d'installation
/// terminée puis nettoie les sauvegardes devenues inutiles (voir
/// <c>InstallationStage</c>/<c>ConfigurationStage</c>, revue adversariale
/// §1/§2) — une fois commité, l'ancien contenu remplacé n'a plus besoin
/// d'être restaurable.
/// </summary>
public sealed class CommitStage : IInstallationStage
{
    public string Name => "commit";

    private sealed record CommitMarker
    {
        public required string ApexVersion { get; init; }
        public required string Profile { get; init; }
        public required string CommittedAtUtc { get; init; }
    }

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var markerPath = Path.Combine(context.DestinationDirectory, ".apex-setup-installed");
        var marker = new CommitMarker
        {
            ApexVersion = context.Plan.TargetApexVersion,
            Profile = context.Plan.SelectedProfile.ToString(),
            CommittedAtUtc = DateTimeOffset.UtcNow.ToString("O"),
        };

        try
        {
            var json = JsonSerializer.Serialize(marker, new JsonSerializerOptions { WriteIndented = true });
            await File.WriteAllTextAsync(markerPath, json, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return StageOutcome.Failed($"écriture du marqueur de commit : {ex.Message}");
        }

        CleanUpBackups(context);
        return StageOutcome.Ok();
    }

    private static void CleanUpBackups(InstallationContext context)
    {
        if (context.TryGetItem<Dictionary<string, string>>(ContextKeys.InstalledFileBackups, out var backups))
        {
            foreach (var backupPath in backups!.Values)
            {
                TryDelete(backupPath);
            }
        }
        if (context.TryGetItem<string>(ContextKeys.ConfigurationBackupPath, out var configBackup) && configBackup is not null)
        {
            TryDelete(configBackup);
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (IOException)
        {
            // Best-effort — une sauvegarde résiduelle après un commit
            // réussi n'est jamais dangereuse, seulement un peu d'espace
            // disque non libéré.
        }
    }

    /// <summary>
    /// No-op délibéré — le pipeline n'appelle de toute façon jamais
    /// RollbackAsync sur la DERNIÈRE étape qui a réussi (rien après elle
    /// ne peut échouer) ; voir <c>Contracts.InstallationStep.IsReversible</c>
    /// = false pour cette étape dans <c>Planning.InstallationPlanBuilder</c>.
    /// </summary>
    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
}
