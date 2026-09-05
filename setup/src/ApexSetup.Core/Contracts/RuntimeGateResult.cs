namespace ApexSetup.Core.Contracts;

/// <summary>
/// Valeurs EXACTES (majuscules) de <c>apex.gate.model.GateStatus</c> — un
/// vocabulaire délibérément différent de <see cref="DoctorSeverity"/>
/// (minuscules) : Doctor et Gate ne mesurent pas la même chose (voir
/// docs/JSON_CONTRACTS.md côté Runtime). Ne jamais faire correspondre l'un
/// à l'autre par convention de casse ou autre heuristique.
/// </summary>
public enum GateStatus
{
    Pass,
    Fail,
    Degraded,
    Unknown,
    Simulated,
}

/// <summary>Miroir de <c>Dependency.to_dict()</c> — les 4 axes exigés côté Runtime.</summary>
public sealed record RuntimeDependencyInfo
{
    public required string Name { get; init; }
    public required string Why { get; init; }
    public required string Install { get; init; }
    public required int RequiredFromPhase { get; init; }
    public required string Fallback { get; init; }
    public required string CheckId { get; init; }
}

/// <summary>Miroir de <c>GateCheckResult.to_dict()</c>.</summary>
public sealed record GateCheckResult
{
    public required string CheckId { get; init; }
    public required string Title { get; init; }
    public required GateStatus Status { get; init; }
    public required string Message { get; init; }
    public required IReadOnlyDictionary<string, object?> Facts { get; init; }
    public required string Remediation { get; init; }
    public required IReadOnlyList<int> BlocksPhases { get; init; }
    public required bool ToValidateOnTarget { get; init; }
    public required string UnknownReason { get; init; }
}

/// <summary>
/// Matériel mesuré — sous-ensemble exact des clés que le Runtime publie
/// dans <c>hardware</c> (<c>apex/gate/report.py</c>). Toutes nullables :
/// une mesure absente (ex. <c>windows_version</c> sur une machine Linux/WSL2)
/// est une donnée manquante réelle, jamais une valeur par défaut inventée.
/// </summary>
public sealed record RuntimeHardwareFacts
{
    public string? DistroName { get; init; }
    public string? Kernel { get; init; }
    public string? CpuModel { get; init; }
    public int? CpuThreads { get; init; }
    public double? RamTotalGb { get; init; }
    public double? DiskFreeGb { get; init; }
    public IReadOnlyList<IReadOnlyDictionary<string, object?>>? Gpus { get; init; }
    public string? DriverVersion { get; init; }
    public string? CudaVersion { get; init; }
    public string? WindowsVersion { get; init; }
}

/// <summary>
/// Miroir de <c>EnvironmentReport.to_dict()</c> (<c>apex gate --json</c>).
/// Contrat mature et versionné de longue date côté Runtime — jamais
/// modifié par cette phase (voir contrainte explicite « Gate reste
/// l'unique autorité de capacité réelle »).
/// </summary>
public sealed record RuntimeGateResult
{
    public required int SchemaVersion { get; init; }
    public required string ApexVersion { get; init; }
    public required string GeneratedAt { get; init; }
    public required string Probe { get; init; }
    public string? MachineLabel { get; init; }
    public required bool MeasuredOnTargetMachine { get; init; }
    public required GateStatus Status { get; init; }
    public required bool Phase2Authorized { get; init; }
    public required string Fingerprint { get; init; }
    public required IReadOnlyDictionary<string, int> Counts { get; init; }
    public required IReadOnlyDictionary<string, object?> Capabilities { get; init; }
    public required RuntimeHardwareFacts Hardware { get; init; }
    public required IReadOnlyList<string> Blocking { get; init; }
    public required IReadOnlyList<string> Unresolved { get; init; }
    public required IReadOnlyList<RuntimeDependencyInfo> MissingDependencies { get; init; }
    public required IReadOnlyList<GateCheckResult> Checks { get; init; }
}
