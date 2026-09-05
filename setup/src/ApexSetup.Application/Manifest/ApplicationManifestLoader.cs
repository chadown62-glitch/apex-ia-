using System.Text.Json;
using ApexSetup.Core.Json;

namespace ApexSetup.Application.Manifest;

/// <summary>
/// Charge le fichier manifeste modèle (donné via <c>--manifest</c>) pour le
/// compte du CLI Setup.
///
/// RÉSERVE EXPLICITE HÉRITÉE DE LA PHASE 1 : Setup ne doit jamais se
/// coupler à un fournisseur de distribution spécifique. <c>Core.Contracts.ModelManifestEntry.Source</c>
/// (voir <c>ModelManifestParser</c>, inchangé ici) reste une étiquette de
/// PROVENANCE (ex. « ollama-registry »), jamais une URL — exactement comme
/// conçu en Phase 1. La construction du plan a pourtant besoin d'une URL de
/// téléchargement RÉELLE par artefact (<c>Planning.InstallationPlanBuilder</c>
/// exige un <c>sourceUrlResolver</c>). Plutôt que d'inventer un fournisseur
/// (Ollama, S3, HTTP statique...) — décision de distribution qui reste hors
/// périmètre de cette phase CLI/Orchestration — ce chargeur lit un second
/// champ, <c>source_url</c>, optionnellement présent sur CHAQUE entrée du
/// même fichier JSON, à côté de <c>source</c>. <c>ModelManifestParser</c>
/// (non modifié) ignore cette clé silencieusement (policy « champs
/// inconnus ignorés », voir docs/JSON_CONTRACTS.md) ; ce chargeur la lit
/// séparément, en plus, jamais à la place. Neutre vis-à-vis du fournisseur :
/// n'importe quel schéma d'URL fonctionne (HTTP statique, S3 pré-signé,
/// etc.), Setup ne décide toujours de rien à ce sujet.
/// </summary>
public static class ApplicationManifestLoader
{
    public static async Task<ManifestLoadResult> LoadAsync(string manifestPath, CancellationToken cancellationToken = default)
    {
        string json;
        try
        {
            json = await File.ReadAllTextAsync(manifestPath, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return ManifestLoadResult.Failed($"lecture du manifeste « {manifestPath} » impossible : {ex.Message}");
        }

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(json);
        }
        catch (JsonException ex)
        {
            return ManifestLoadResult.Failed($"manifeste « {manifestPath} » n'est pas un JSON valide : {ex.Message}");
        }

        using (document)
        {
            try
            {
                var entries = ModelManifestParser.ParseManifest(document.RootElement);

                var itemsArray = document.RootElement.ValueKind == JsonValueKind.Array
                    ? document.RootElement
                    : document.RootElement.TryGetProperty("models", out var modelsProp) ? modelsProp : default;

                var urls = new Dictionary<string, string>(StringComparer.Ordinal);
                if (itemsArray.ValueKind == JsonValueKind.Array)
                {
                    foreach (var item in itemsArray.EnumerateArray())
                    {
                        if (item.TryGetProperty("model_id", out var idProp) && idProp.ValueKind == JsonValueKind.String
                            && item.TryGetProperty("source_url", out var urlProp) && urlProp.ValueKind == JsonValueKind.String)
                        {
                            urls[idProp.GetString()!] = urlProp.GetString()!;
                        }
                    }
                }

                return ManifestLoadResult.Ok(entries, urls);
            }
            catch (ContractValidationException ex)
            {
                return ManifestLoadResult.Failed($"manifeste « {manifestPath} » invalide : {ex.Message}");
            }
        }
    }
}
