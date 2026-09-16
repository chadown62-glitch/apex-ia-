using System.Text.Json;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Persistance JSON dans le profil utilisateur — jamais <c>Program Files</c>
/// (en écriture restreinte sans élévation) ni à côté de l'exécutable
/// (perdu à chaque réinstallation). <see cref="DashboardSettings"/> ne
/// porte aucun secret (voir sa docstring) : aucun chiffrement n'est
/// nécessaire ici, contrairement à un futur fichier qui en porterait un.
/// </summary>
public sealed class JsonSettingsStore(string filePath) : ISettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public static string DefaultFilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Apex", "dashboard-settings.json");

    public async Task<DashboardSettings> LoadAsync(CancellationToken cancellationToken = default)
    {
        if (!File.Exists(filePath))
        {
            return new DashboardSettings();
        }

        try
        {
            await using var stream = File.OpenRead(filePath);
            var settings = await JsonSerializer.DeserializeAsync<DashboardSettings>(stream, JsonOptions, cancellationToken)
                .ConfigureAwait(false);
            return settings ?? new DashboardSettings();
        }
        catch (JsonException)
        {
            // Fichier corrompu/illisible — jamais un crash au démarrage,
            // repli sur les valeurs par défaut (jamais écrasé
            // silencieusement : SaveAsync n'est appelé qu'à une action
            // explicite de l'utilisateur en Paramètres).
            return new DashboardSettings();
        }
    }

    public async Task SaveAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        var directory = Path.GetDirectoryName(filePath);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        // Écriture atomique (fichier temporaire + remplacement) — même
        // discipline que apex/api/auth.py::_write_token_file : jamais un
        // fichier partiellement écrit visible par une lecture concurrente.
        var tempPath = filePath + ".tmp";
        await using (var stream = File.Create(tempPath))
        {
            await JsonSerializer.SerializeAsync(stream, settings, JsonOptions, cancellationToken).ConfigureAwait(false);
        }
        File.Move(tempPath, filePath, overwrite: true);
    }
}
