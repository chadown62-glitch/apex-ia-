namespace ApexSetup.Core.Contracts;

/// <summary>
/// Miroir exact de <c>apex.version.build_info()</c> (voir <c>apex version --json</c>).
/// Ce contrat n'a pas de <c>schema_version</c> côté Runtime — il est stable
/// depuis la Phase 1 du dépôt DEV et n'a jamais changé de forme.
/// </summary>
public sealed record RuntimeVersionResult
{
    public required string Version { get; init; }
    public required int Phase { get; init; }
    public required string PhaseName { get; init; }
    public required string Python { get; init; }
    public required string Platform { get; init; }
    public required string Executable { get; init; }
}
