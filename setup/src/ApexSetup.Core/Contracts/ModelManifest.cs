namespace ApexSetup.Core.Contracts;

/// <summary>
/// Un modèle déclaré — les champs reprennent exactement ceux déjà présents
/// dans <c>apex.config.schema.ModelRef</c>/<c>ModelEndpoint</c>
/// (<c>family</c>→<c>ModelId</c>, <c>version</c>, <c>quantization</c>,
/// <c>capability_class</c>, <c>served_model</c>, <c>max_context</c>,
/// <c>vram_gb</c>) plus les champs NOUVEAUX nécessaires à Setup
/// (<c>SizeBytes</c>, <c>Sha256</c>, <c>RequiredRamGb</c>,
/// <c>SupportedProfiles</c>, <c>FallbackModelId</c>, <c>Source</c>) — un
/// manifeste séparé, jamais une modification du schéma Runtime.
/// </summary>
public sealed record ModelManifestEntry
{
    public required string ModelId { get; init; }
    public required string Version { get; init; }
    public required string Quantization { get; init; }
    public required string ServedModel { get; init; }
    public required long SizeBytes { get; init; }
    public required string Sha256 { get; init; }
    public required double RequiredVramGb { get; init; }
    public required double RequiredRamGb { get; init; }
    public required int MaxContext { get; init; }
    public required string CapabilityClass { get; init; }
    public required IReadOnlyList<HardwareProfileTier> SupportedProfiles { get; init; }
    public string? FallbackModelId { get; init; }
    public required string Source { get; init; }
}
