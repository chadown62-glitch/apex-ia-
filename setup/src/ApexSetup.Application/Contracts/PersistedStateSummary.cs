namespace ApexSetup.Application.Contracts;

/// <summary>Miroir JSON de <c>Pipeline.InstallationState</c> — reste un type CLI séparé pour ne jamais coupler la forme du contrat public à la forme interne du Core.</summary>
public sealed record PersistedStateSummary
{
    public required string PlanFingerprint { get; init; }
    public required IReadOnlyList<string> RequiredArtifactIds { get; init; }
    public required IReadOnlyList<string> CompletedStageNames { get; init; }
    public required string Phase { get; init; }
    public required string UpdatedAtUtc { get; init; }
}
