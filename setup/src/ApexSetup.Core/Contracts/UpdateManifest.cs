namespace ApexSetup.Core.Contracts;

/// <summary>
/// Manifeste de mise à jour — miroir du pipeline documenté en conception :
/// Installed Version → Update Manifest → Download → Verify Hash → Stage →
/// Validate → Activate → Previous Version retained → Rollback si échec.
/// Donnée pure dans cette phase, aucune logique de mise à jour exécutée.
/// </summary>
public sealed record UpdateManifest
{
    public required string TargetSetupVersion { get; init; }
    public required string TargetApexVersion { get; init; }
    public required string TargetRuntimeSourceSha { get; init; }
    public required string TargetRuntimeArtifactHash { get; init; }
    public required IReadOnlyList<ModelManifestEntry> TargetModels { get; init; }
    public required IReadOnlyDictionary<string, string> TargetDependencyVersions { get; init; }
}

/// <summary>État de rollback — la version précédente n'est jamais supprimée avant qu'Activate ait réussi (règle verrouillée en conception, section H).</summary>
public sealed record RollbackState
{
    public required string PreviousSetupVersion { get; init; }
    public required string PreviousApexVersion { get; init; }
    public required string PreviousRuntimeArtifactPath { get; init; }
    public required bool PreviousVersionStillIntact { get; init; }
    public required DateTimeOffset RetainedSince { get; init; }
}
