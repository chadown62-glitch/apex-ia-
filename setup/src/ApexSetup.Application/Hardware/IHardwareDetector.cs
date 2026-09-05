namespace ApexSetup.Application.Hardware;

/// <summary>
/// Résultat brut d'une détection matérielle locale — donnée d'entrée pure
/// pour <c>Core.Hardware.HardwareProfileEngine.SelectRecommendedProfile</c>
/// (qui attend un <c>Core.Contracts.DetectedHardware</c>) ET pour
/// l'affichage direct de <c>setup detect</c>. GPU/VRAM sont honnêtement
/// nullables — voir <see cref="RealHardwareDetector"/>.
/// </summary>
public sealed record DetectedMachine
{
    public required string Os { get; init; }
    public required string Architecture { get; init; }
    public required double RamTotalGb { get; init; }
    public required int CpuThreads { get; init; }
    public string? Gpu { get; init; }
    public double? VramTotalGb { get; init; }
    public required string Environment { get; init; }
}

/// <summary>
/// Abstraction de la détection matérielle LOCALE — jamais le Gate (mesure
/// distincte, faisant autorité, voir <c>RuntimeGateResult</c>). Une seule
/// implémentation réelle (<see cref="RealHardwareDetector"/>) : pas
/// besoin d'un double de test dédié, chaque champ qu'elle expose provient
/// d'une API .NET stable et déterministe pour un test (nombre de coeurs,
/// architecture du processus) — voir <c>HardwareDetectorTests</c>.
/// </summary>
public interface IHardwareDetector
{
    DetectedMachine Detect();
}
