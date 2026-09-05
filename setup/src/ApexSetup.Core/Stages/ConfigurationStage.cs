using System.Text.Json;
using ApexSetup.Core.Pipeline;

namespace ApexSetup.Core.Stages;

/// <summary>
/// Étape 4 : écrit la configuration utilisateur minimale dérivée du plan
/// (jamais un secret — aucune valeur sensible n'existe à ce niveau).
///
/// CORRIGÉ EN REVUE ADVERSARIALE (§1/§2) : si une configuration existait
/// déjà (réinstallation), elle est sauvegardée avant d'être écrasée ; le
/// rollback restaure ce contenu au lieu de le supprimer.
/// </summary>
public sealed class ConfigurationStage : IInstallationStage
{
    public string Name => "configuration";

    private sealed record ConfigurationDocument
    {
        public required string ApexVersion { get; init; }
        public required string Profile { get; init; }
        public required IReadOnlyList<string> InstalledArtifactIds { get; init; }
    }

    public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        var configPath = Path.Combine(context.DestinationDirectory, "apex-user-config.json");
        var document = new ConfigurationDocument
        {
            ApexVersion = context.Plan.TargetApexVersion,
            Profile = context.Plan.SelectedProfile.ToString(),
            InstalledArtifactIds = context.Plan.RequiredArtifacts.Select(a => a.ArtifactId).OrderBy(id => id, StringComparer.Ordinal).ToList(),
        };

        var preExisted = File.Exists(configPath);
        string? backupPath = null;

        try
        {
            Directory.CreateDirectory(context.DestinationDirectory);
            if (preExisted)
            {
                backupPath = configPath + ".installation-backup";
                File.Copy(configPath, backupPath, overwrite: true);
            }
            var json = JsonSerializer.Serialize(document, new JsonSerializerOptions { WriteIndented = true });
            var tempPath = configPath + ".writing";
            await File.WriteAllTextAsync(tempPath, json, cancellationToken).ConfigureAwait(false);
            File.Move(tempPath, configPath, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            if (backupPath is not null)
            {
                RestoreBackup(configPath, backupPath);
            }
            return StageOutcome.Failed($"écriture de la configuration : {ex.Message}");
        }

        context.SetItem(ContextKeys.ConfigurationFilePath, configPath);
        if (backupPath is not null)
        {
            context.SetItem(ContextKeys.ConfigurationBackupPath, backupPath);
        }
        return StageOutcome.Ok();
    }

    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        if (!context.TryGetItem<string>(ContextKeys.ConfigurationFilePath, out var path))
        {
            return Task.CompletedTask;
        }

        if (context.TryGetItem<string>(ContextKeys.ConfigurationBackupPath, out var backupPath) && backupPath is not null)
        {
            RestoreBackup(path!, backupPath);
        }
        else
        {
            TryDelete(path!);
        }
        return Task.CompletedTask;
    }

    private static void RestoreBackup(string finalPath, string backupPath)
    {
        try
        {
            if (File.Exists(backupPath))
            {
                File.Move(backupPath, finalPath, overwrite: true);
            }
        }
        catch (IOException)
        {
            // Best-effort — voir InstallationPipeline.RollbackAsync.
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
            // Best-effort — voir InstallationPipeline.RollbackAsync.
        }
    }
}
