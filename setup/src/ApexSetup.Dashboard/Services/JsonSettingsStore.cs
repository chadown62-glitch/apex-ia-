using System.Text.Json;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Persistance JSON dans le profil utilisateur (<see cref="PanelInfo.SettingsFile"/>) — jamais à côté
/// de l'exécutable (remplacé à chaque mise à jour) ni dans un dossier protégé. Un fichier corrompu
/// ne fait jamais planter le Panel : valeurs par défaut, problème rapporté au diagnostic, et le
/// fichier n'est jamais écrasé silencieusement (seul « Enregistrer » écrit).
/// </summary>
public sealed class JsonSettingsStore(string filePath) : ISettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public async Task<SettingsLoad> LoadAsync(CancellationToken cancellationToken = default)
    {
        if (!File.Exists(filePath))
        {
            return new SettingsLoad(new DashboardSettings(), null);
        }

        try
        {
            await using var stream = File.OpenRead(filePath);
            var settings = await JsonSerializer.DeserializeAsync<DashboardSettings>(stream, JsonOptions, cancellationToken)
                .ConfigureAwait(false);
            if (settings?.ApiPort is < 1 or > 65535)
            {
                return new SettingsLoad(new DashboardSettings(), $"port hors bornes ({settings.ApiPort}) ignoré");
            }
            return new SettingsLoad(settings ?? new DashboardSettings(), settings is null ? "fichier vide : valeurs par défaut" : null);
        }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException or NotSupportedException)
        {
            return new SettingsLoad(new DashboardSettings(), $"paramètres illisibles, valeurs par défaut utilisées ({ex.GetType().Name})");
        }
    }

    public async Task SaveAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        var directory = Path.GetDirectoryName(filePath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        // Écriture atomique (fichier temporaire + remplacement) : jamais un fichier à moitié écrit.
        var tempPath = filePath + ".tmp";
        await using (var stream = File.Create(tempPath))
        {
            await JsonSerializer.SerializeAsync(stream, settings, JsonOptions, cancellationToken).ConfigureAwait(false);
        }
        File.Move(tempPath, filePath, overwrite: true);
    }
}
