namespace ApexSetup.Core.Contracts;

/// <summary>
/// Les 4 profils — RECOMMANDATION PRODUIT UNIQUEMENT. Ne fait jamais
/// autorité seul : Gate mesure et autorise réellement (voir
/// docs de conception « APEX SETUP », section B : Hardware Detection →
/// Hardware Profile → Recommended Model → Gate verification).
/// </summary>
public enum HardwareProfileTier
{
    Minimal,
    Standard,
    Performance,
    HighEnd,
}

/// <summary>
/// Un profil matériel déclaratif — les valeurs numériques sont des seuils
/// de RECOMMANDATION Setup, distincts des seuils Gate réels
/// (<c>apex.config.schema.HardwareThresholds</c>, jamais modifiés ici).
/// </summary>
public sealed record HardwareProfile
{
    public required HardwareProfileTier Tier { get; init; }
    public required double RecommendedRamGb { get; init; }
    public required double RecommendedVramGb { get; init; }
    public required int RecommendedCpuThreads { get; init; }
    public required double RecommendedDiskGb { get; init; }
    public required string PrimaryModelId { get; init; }
    public string? FallbackModelId { get; init; }
    public required string EmbeddingModelId { get; init; }
    public required int RecommendedContextTokens { get; init; }
    public required int RecommendedConcurrentAgents { get; init; }
    public required string UsageDescription { get; init; }
}

/// <summary>Matériel détecté localement par Setup (natif Windows, hors périmètre de cette phase — représenté ici comme donnée d'entrée pure).</summary>
public sealed record DetectedHardware
{
    public required double RamTotalGb { get; init; }
    public required double VramTotalGb { get; init; }
    public required int CpuThreads { get; init; }
    public required double DiskFreeGb { get; init; }
}
