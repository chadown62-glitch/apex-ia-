using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;

namespace ApexSetup.Core.Planning;

/// <summary>
/// Construit un <see cref="InstallationPlan"/> DÉTERMINISTE : la même
/// entrée produit toujours exactement la même sortie (voir
/// <c>InstallationPlanBuilderTests.Same_input_produces_an_identical_plan</c>).
/// Aucune horloge, aucun GUID, aucun ordre de <c>Dictionary</c>/<c>HashSet</c>
/// non trié n'entre dans le résultat — chaque collection est explicitement
/// triée avant d'être matérialisée.
///
/// Ne couvre QUE les artefacts modèles pour <see cref="InstallationPlan.RequiredArtifacts"/>
/// dans cette phase — le téléchargement de l'artefact Runtime lui-même
/// suppose un manifeste de release (pipeline décrit en conception, non
/// encore construit côté outillage `runtime_build/`) ; limitation
/// documentée, pas un oubli silencieux.
/// </summary>
public static class InstallationPlanBuilder
{
    public static InstallationPlan Build(
        string targetApexVersion,
        HardwareProfileTier selectedProfile,
        IReadOnlyList<ModelManifestEntry> availableModels,
        string destination,
        IReadOnlyDictionary<string, DependencyRequirement> dependencies,
        Func<ModelManifestEntry, string> sourceUrlResolver)
    {
        var profile = HardwareProfileCatalog.Profiles[selectedProfile];
        var neededModelIds = new List<string> { profile.PrimaryModelId, profile.EmbeddingModelId };
        if (profile.FallbackModelId is not null)
        {
            neededModelIds.Add(profile.FallbackModelId);
        }
        var neededSet = neededModelIds.ToHashSet(StringComparer.Ordinal);

        var requiredModels = availableModels
            .Where(m => neededSet.Contains(m.ModelId))
            .OrderBy(m => m.ModelId, StringComparer.Ordinal)
            .ToList();

        var requiredArtifacts = requiredModels
            .Select(m => new RequiredArtifact
            {
                ArtifactId = m.ModelId,
                SourceUrl = sourceUrlResolver(m),
                SizeBytes = m.SizeBytes,
                Sha256 = m.Sha256,
            })
            .OrderBy(a => a.ArtifactId, StringComparer.Ordinal)
            .ToList();

        var requiredDiskBytes = requiredModels.Sum(m => m.SizeBytes);

        var sortedDependencies = dependencies
            .OrderBy(kv => kv.Key, StringComparer.Ordinal)
            .ToDictionary(kv => kv.Key, kv => kv.Value);

        return new InstallationPlan
        {
            TargetApexVersion = targetApexVersion,
            SelectedProfile = selectedProfile,
            RequiredArtifacts = requiredArtifacts,
            RequiredModels = requiredModels,
            Destination = destination,
            RequiredDiskBytes = requiredDiskBytes,
            Steps = BuildSteps(),
            Dependencies = sortedDependencies,
        };
    }

    private static IReadOnlyList<InstallationStep> BuildSteps() =>
    [
        new InstallationStep
        {
            Id = "download", Title = "Téléchargement des artefacts", Order = 1,
            RequiresElevation = false, IsRetryable = true, RequiresRollbackOnFailure = true, IsReversible = true,
        },
        new InstallationStep
        {
            Id = "integrity_verification", Title = "Vérification d'intégrité (SHA-256)", Order = 2,
            RequiresElevation = false, IsRetryable = true, RequiresRollbackOnFailure = false, IsReversible = true,
        },
        new InstallationStep
        {
            Id = "installation", Title = "Installation des artefacts vérifiés", Order = 3,
            RequiresElevation = false, IsRetryable = true, RequiresRollbackOnFailure = true, IsReversible = true,
        },
        new InstallationStep
        {
            Id = "configuration", Title = "Génération de la configuration utilisateur", Order = 4,
            RequiresElevation = false, IsRetryable = true, RequiresRollbackOnFailure = true, IsReversible = true,
        },
        new InstallationStep
        {
            Id = "validation", Title = "Validation (Doctor/Gate finaux)", Order = 5,
            RequiresElevation = false, IsRetryable = true, RequiresRollbackOnFailure = false, IsReversible = true,
        },
        new InstallationStep
        {
            // Point de non-retour explicite — voir Pipeline/CommitStage.
            Id = "commit", Title = "Finalisation de l'installation", Order = 6,
            RequiresElevation = false, IsRetryable = false, RequiresRollbackOnFailure = false, IsReversible = false,
        },
    ];
}
