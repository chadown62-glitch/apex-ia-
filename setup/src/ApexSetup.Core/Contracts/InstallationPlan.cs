namespace ApexSetup.Core.Contracts;

/// <summary>Catégorie d'une dépendance dans le plan d'installation — vocabulaire verrouillé en conception (document « APEX SETUP »).</summary>
public enum DependencyRequirement
{
    Required,
    Optional,
    AlreadyInstalled,
    Missing,
    Incompatible,
    Unknown,
}

/// <summary>Une étape du pipeline d'installation (16 étapes documentées en conception) — donnée pure.</summary>
public sealed record InstallationStep
{
    public required string Id { get; init; }
    public required string Title { get; init; }
    public required int Order { get; init; }
    public required bool RequiresElevation { get; init; }
    public required bool IsRetryable { get; init; }
    public required bool RequiresRollbackOnFailure { get; init; }

    /// <summary>
    /// False pour une opération qui ne peut pas être proprement défaite une
    /// fois exécutée (ex. Commit — voir Pipeline/CommitStage). Une étape
    /// non réversible doit être la DERNIÈRE du plan, jamais suivie d'une
    /// étape susceptible d'échouer et de déclencher un rollback qui
    /// l'atteindrait.
    /// </summary>
    public required bool IsReversible { get; init; }
}

/// <summary>Un artefact (Runtime ou modèle) requis par le plan, avec sa source de vérité pour l'intégrité.</summary>
public sealed record RequiredArtifact
{
    public required string ArtifactId { get; init; }
    public required string SourceUrl { get; init; }
    public required long SizeBytes { get; init; }
    public required string Sha256 { get; init; }
}

/// <summary>
/// Le plan complet — DÉTERMINISTE : construit par
/// <c>Planning.InstallationPlanBuilder</c>, la même entrée produit
/// toujours exactement le même plan (voir
/// <c>InstallationPlanBuilderTests.Same_input_produces_an_identical_plan</c>).
/// Aucune horloge, aucun GUID, aucun ordre de dictionnaire non trié
/// n'entre dans sa construction.
/// </summary>
public sealed record InstallationPlan
{
    public required string TargetApexVersion { get; init; }
    public required HardwareProfileTier SelectedProfile { get; init; }
    public required IReadOnlyList<RequiredArtifact> RequiredArtifacts { get; init; }
    public required IReadOnlyList<ModelManifestEntry> RequiredModels { get; init; }
    public required string Destination { get; init; }
    public required long RequiredDiskBytes { get; init; }
    public required IReadOnlyList<InstallationStep> Steps { get; init; }
    public required IReadOnlyDictionary<string, DependencyRequirement> Dependencies { get; init; }

    /// <summary>Alias explicite de <see cref="SelectedProfile"/> — nom retenu en conception initiale, conservé pour compatibilité de lecture.</summary>
    public HardwareProfileTier RecommendedProfile => SelectedProfile;
}

/// <summary>Résultat de l'exécution d'une étape (ou de l'ensemble) — donnée pure.</summary>
public sealed record InstallationResult
{
    public required string StepId { get; init; }
    public required bool Success { get; init; }
    public string? ErrorMessage { get; init; }
    public required DateTimeOffset CompletedAt { get; init; }
}

/// <summary>Résultat d'une validation (Doctor/Gate final, vérification d'intégrité) — jamais un simple booléen, toujours un détail exploitable.</summary>
public sealed record ValidationResult
{
    public required bool Success { get; init; }
    public required IReadOnlyList<string> Errors { get; init; }
    public required IReadOnlyList<string> Warnings { get; init; }
}
