using System.Text.Json;
using ApexSetup.Core.Contracts;
using static ApexSetup.Core.Json.JsonHelpers;

namespace ApexSetup.Core.Json;

/// <summary>
/// Parseur strict du manifeste modèle (voir <see cref="ModelManifestEntry"/>)
/// — même discipline que <see cref="RuntimeContractParser"/> : champs
/// obligatoires/types vérifiés explicitement, jamais une désérialisation
/// aveugle qui masquerait une entrée corrompue derrière une valeur par
/// défaut.
/// </summary>
public static class ModelManifestParser
{
    private static readonly IReadOnlyDictionary<string, HardwareProfileTier> ProfileValues =
        new Dictionary<string, HardwareProfileTier>
        {
            ["MINIMAL"] = HardwareProfileTier.Minimal,
            ["STANDARD"] = HardwareProfileTier.Standard,
            ["PERFORMANCE"] = HardwareProfileTier.Performance,
            ["HIGH_END"] = HardwareProfileTier.HighEnd,
        };

    public static ModelManifestEntry ParseEntry(JsonElement item)
    {
        var supportedProfiles = RequireArray(item, "supported_profiles").EnumerateArray()
            .Select(e =>
            {
                var raw = e.ValueKind == JsonValueKind.String
                    ? e.GetString()!
                    : throw new ContractValidationException(
                        RuntimeAdapter.RuntimeCallOutcome.UnexpectedFieldType,
                        $"« supported_profiles[] » attendu chaîne, reçu {e.ValueKind}");
                if (!ProfileValues.TryGetValue(raw, out var tier))
                {
                    throw new ContractValidationException(
                        RuntimeAdapter.RuntimeCallOutcome.InvalidEnumValue,
                        $"« supported_profiles » : valeur inconnue « {raw} »");
                }
                return tier;
            })
            .ToList();

        return new ModelManifestEntry
        {
            ModelId = RequireString(item, "model_id"),
            Version = RequireString(item, "version"),
            Quantization = RequireString(item, "quantization"),
            ServedModel = RequireString(item, "served_model"),
            SizeBytes = (long)RequireDouble(item, "size_bytes"),
            Sha256 = RequireString(item, "sha256"),
            RequiredVramGb = RequireDouble(item, "required_vram_gb"),
            RequiredRamGb = RequireDouble(item, "required_ram_gb"),
            MaxContext = RequireInt(item, "max_context"),
            CapabilityClass = RequireString(item, "capability_class"),
            SupportedProfiles = supportedProfiles,
            FallbackModelId = OptionalString(item, "fallback_model_id"),
            Source = RequireString(item, "source"),
        };
    }

    public static IReadOnlyList<ModelManifestEntry> ParseManifest(JsonElement root)
    {
        var entriesArray = root.ValueKind == JsonValueKind.Array
            ? root
            : RequireArray(root, "models");
        return entriesArray.EnumerateArray().Select(ParseEntry).ToList();
    }
}
