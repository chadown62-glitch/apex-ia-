using ApexSetup.Core.Contracts;

namespace ApexSetup.Application.Contracts;

// ---------------------------------------------------------------------
// `setup version`
// ---------------------------------------------------------------------

public sealed record RuntimeVersionSummary
{
    public required string Version { get; init; }
    public required int Phase { get; init; }
    public required string PhaseName { get; init; }
    public required string Python { get; init; }
    public required string Platform { get; init; }
}

public sealed record VersionResponse : CliResponseEnvelope
{
    public required string SetupVersion { get; init; }
    public RuntimeVersionSummary? Runtime { get; init; }
}

// ---------------------------------------------------------------------
// `setup detect`
// ---------------------------------------------------------------------

/// <summary>
/// GPU/VRAM sont délibérément nullables et honnêtement <c>null</c> dans
/// cette phase : aucune détection matérielle GPU fiable et multiplateforme
/// n'existe encore dans ce dépôt (la détection réelle passe par des API
/// natives Windows — WMI — hors périmètre explicite de cette phase, voir
/// <c>Core.Contracts.DetectedHardware</c> déjà documenté comme tel en
/// Phase 1/2). Représenter une valeur inconnue par une fausse mesure
/// serait plus dangereux qu'un <c>null</c> honnête.
/// </summary>
public sealed record DetectResponse : CliResponseEnvelope
{
    public required string Os { get; init; }
    public required string Architecture { get; init; }
    public required double RamTotalGb { get; init; }
    public required int CpuThreads { get; init; }
    public string? Gpu { get; init; }
    public double? VramTotalGb { get; init; }
    public required double DiskFreeGb { get; init; }
    public required string Environment { get; init; }
    public required string RecommendedProfile { get; init; }
    public required string ProfileNote { get; init; }
}

// ---------------------------------------------------------------------
// `setup check`
// ---------------------------------------------------------------------

public sealed record CheckItem
{
    public required string Name { get; init; }
    public required bool Passed { get; init; }
    public required bool Blocking { get; init; }
    public required string Detail { get; init; }
}

public sealed record CheckResponse : CliResponseEnvelope
{
    public required IReadOnlyList<CheckItem> Checks { get; init; }
    public required bool HasBlockingFailure { get; init; }
}

// ---------------------------------------------------------------------
// `setup plan`
// ---------------------------------------------------------------------

public sealed record PlanResponse : CliResponseEnvelope
{
    /// <summary>Null uniquement si <c>Success</c> est false (manifeste illisible, URL de source manquante, etc. — voir <c>Errors</c>).</summary>
    public InstallationPlan? Plan { get; init; }
    public string? PlanFingerprint { get; init; }
}

// ---------------------------------------------------------------------
// `setup install`
// ---------------------------------------------------------------------

public sealed record InstallResponse : CliResponseEnvelope
{
    public required bool Committed { get; init; }
    public required IReadOnlyList<string> ExecutedSteps { get; init; }
    public string? FailedStep { get; init; }
    public string? FailureDetail { get; init; }
    public required IReadOnlyList<string> RolledBackSteps { get; init; }
    public required IReadOnlyList<string> FailedRollbackSteps { get; init; }
}

// ---------------------------------------------------------------------
// `setup recover`
// ---------------------------------------------------------------------

public sealed record RecoverResponse : CliResponseEnvelope
{
    public required string StateDetected { get; init; }
    public PersistedStateSummary? PersistedState { get; init; }
    public required string ActionTaken { get; init; }
    public required string Result { get; init; }
    public required string Detail { get; init; }
}

// ---------------------------------------------------------------------
// `setup validate`
// ---------------------------------------------------------------------

public sealed record DoctorSummary
{
    public required string Status { get; init; }
    public required IReadOnlyDictionary<string, int> Counts { get; init; }
    public required int ResultsCount { get; init; }
}

public sealed record GateSummary
{
    public required string Status { get; init; }
    public required bool Phase2Authorized { get; init; }
    public required IReadOnlyList<string> Blocking { get; init; }
    public required IReadOnlyList<string> Unresolved { get; init; }
}

public sealed record ValidateResponse : CliResponseEnvelope
{
    public required bool RuntimeReachable { get; init; }
    public RuntimeVersionSummary? Version { get; init; }
    public DoctorSummary? Doctor { get; init; }
    public GateSummary? Gate { get; init; }
}

// ---------------------------------------------------------------------
// `setup status`
// ---------------------------------------------------------------------

public sealed record StatusResponse : CliResponseEnvelope
{
    public required bool LockCurrentlyHeld { get; init; }
    public PersistedStateSummary? PersistedState { get; init; }
    public required string Detail { get; init; }
}
