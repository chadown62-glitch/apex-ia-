using System.Text.Json;
using System.Text.Json.Serialization;

namespace ApexSetup.Release;

/// <summary>Sérialisation JSON du manifeste — même convention que le contrat CLI (snake_case, jamais un entier brut pour un enum) pour rester cohérent dans tout le produit Setup.</summary>
public static class ReleaseManifestJson
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static string Serialize(ReleaseManifest manifest) => JsonSerializer.Serialize(manifest, Options);

    public static async Task WriteAsync(string path, ReleaseManifest manifest, CancellationToken cancellationToken = default)
    {
        var json = Serialize(manifest);
        var tempPath = path + ".writing";
        await File.WriteAllTextAsync(tempPath, json, cancellationToken).ConfigureAwait(false);
        File.Move(tempPath, path, overwrite: true);
    }

    /// <summary>Retourne <c>null</c> si le fichier est absent, illisible ou structurellement invalide — jamais une exception qui remonterait à un appelant qui ne s'y attend pas (voir <see cref="ReleaseManifestValidator"/>, qui distingue explicitement ce cas).</summary>
    public static ReleaseManifest? TryRead(string path)
    {
        if (!File.Exists(path))
        {
            return null;
        }
        try
        {
            var manifest = JsonSerializer.Deserialize<ReleaseManifest>(File.ReadAllText(path), Options);
            // required properties non satisfaites -> JsonException à la désérialisation en .NET 8 ;
            // filet de sécurité si un futur changement de type le rendait silencieux.
            return manifest;
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
