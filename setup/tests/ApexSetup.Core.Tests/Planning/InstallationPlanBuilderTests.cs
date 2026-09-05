using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.Planning;
using Xunit;

namespace ApexSetup.Core.Tests.Planning;

public class InstallationPlanBuilderTests
{
    private static ModelManifestEntry Model(string id, long size = 1000, HardwareProfileTier[]? profiles = null) => new()
    {
        ModelId = id,
        Version = "1",
        Quantization = "q4_k_m",
        ServedModel = id,
        SizeBytes = size,
        Sha256 = new string('a', 64),
        RequiredVramGb = 1,
        RequiredRamGb = 1,
        MaxContext = 8192,
        CapabilityClass = "coder",
        SupportedProfiles = profiles ?? [HardwareProfileTier.Standard],
        Source = "test",
    };

    private static readonly IReadOnlyList<ModelManifestEntry> AllModels =
    [
        Model(HardwareProfileCatalog.ModelIdCoderStandard, size: 14_000),
        Model(HardwareProfileCatalog.ModelIdJudgePrimary, size: 8_000),
        Model(HardwareProfileCatalog.ModelIdFast, size: 5_000),
        Model(HardwareProfileCatalog.ModelIdEmbed, size: 600),
        Model("unrelated-model", size: 999_999), // ne doit jamais apparaître dans un plan STANDARD
    ];

    private static string ResolveUrl(ModelManifestEntry m) => $"https://models.invalid/{m.ModelId}";

    [Fact]
    public void Build_selects_only_the_models_the_profile_actually_needs()
    {
        var plan = InstallationPlanBuilder.Build(
            "0.1.2", HardwareProfileTier.Standard, AllModels, "/dest",
            new Dictionary<string, DependencyRequirement>(), ResolveUrl);

        var ids = plan.RequiredModels.Select(m => m.ModelId).ToList();
        Assert.Contains(HardwareProfileCatalog.ModelIdCoderStandard, ids);
        Assert.Contains(HardwareProfileCatalog.ModelIdEmbed, ids);
        Assert.DoesNotContain("unrelated-model", ids);
    }

    [Fact]
    public void Build_computes_required_disk_bytes_as_the_sum_of_selected_models()
    {
        var plan = InstallationPlanBuilder.Build(
            "0.1.2", HardwareProfileTier.Standard, AllModels, "/dest",
            new Dictionary<string, DependencyRequirement>(), ResolveUrl);

        var expected = plan.RequiredModels.Sum(m => m.SizeBytes);
        Assert.Equal(expected, plan.RequiredDiskBytes);
        Assert.True(plan.RequiredDiskBytes > 0);
    }

    [Fact]
    public void Same_input_produces_an_identical_plan()
    {
        // Comparaison champ par champ, jamais `Assert.Equal(planA, planB)`
        // seul : `InstallationPlan` est un `record` mais certains de ses
        // champs sont typés `List<T>` (égalité par RÉFÉRENCE, `List<T>` ne
        // surcharge pas `Equals`) — l'égalité générée par le record
        // échouerait donc même pour deux plans au contenu strictement
        // identique. La comparaison ci-dessous vérifie le CONTENU, ce qui
        // est la propriété réellement exigée par « même entrée -> même
        // sortie ».
        var deps = new Dictionary<string, DependencyRequirement> { ["docker"] = DependencyRequirement.Required };

        var planA = InstallationPlanBuilder.Build("0.1.2", HardwareProfileTier.Standard, AllModels, "/dest", deps, ResolveUrl);
        var planB = InstallationPlanBuilder.Build("0.1.2", HardwareProfileTier.Standard, AllModels, "/dest", deps, ResolveUrl);

        Assert.Equal(planA.TargetApexVersion, planB.TargetApexVersion);
        Assert.Equal(planA.SelectedProfile, planB.SelectedProfile);
        Assert.Equal(planA.Destination, planB.Destination);
        Assert.Equal(planA.RequiredDiskBytes, planB.RequiredDiskBytes);
        Assert.Equal(planA.RequiredArtifacts, planB.RequiredArtifacts);
        Assert.Equal(planA.RequiredModels, planB.RequiredModels);
        Assert.Equal(planA.Steps, planB.Steps);
        Assert.Equal(planA.Dependencies, planB.Dependencies);
    }

    [Fact]
    public void Build_produces_steps_in_the_exact_pipeline_order_with_commit_last_and_irreversible()
    {
        var plan = InstallationPlanBuilder.Build(
            "0.1.2", HardwareProfileTier.Standard, AllModels, "/dest",
            new Dictionary<string, DependencyRequirement>(), ResolveUrl);

        Assert.Equal(
            ["download", "integrity_verification", "installation", "configuration", "validation", "commit"],
            plan.Steps.OrderBy(s => s.Order).Select(s => s.Id));
        var commit = plan.Steps.Single(s => s.Id == "commit");
        Assert.False(commit.IsReversible);
        Assert.Equal(commit.Order, plan.Steps.Max(s => s.Order));
    }

    [Fact]
    public void Build_sorts_artifacts_and_dependencies_deterministically_regardless_of_input_order()
    {
        var reversedModels = AllModels.Reverse().ToList();
        var deps = new Dictionary<string, DependencyRequirement>
        {
            ["zzz"] = DependencyRequirement.Optional, ["aaa"] = DependencyRequirement.Required,
        };

        var plan = InstallationPlanBuilder.Build("0.1.2", HardwareProfileTier.Standard, reversedModels, "/dest", deps, ResolveUrl);

        Assert.Equal(plan.RequiredArtifacts.Select(a => a.ArtifactId).OrderBy(x => x, StringComparer.Ordinal),
            plan.RequiredArtifacts.Select(a => a.ArtifactId));
        Assert.Equal(["aaa", "zzz"], plan.Dependencies.Keys);
    }

    [Fact]
    public void RecommendedProfile_alias_matches_SelectedProfile()
    {
        var plan = InstallationPlanBuilder.Build(
            "0.1.2", HardwareProfileTier.HighEnd, AllModels, "/dest",
            new Dictionary<string, DependencyRequirement>(), ResolveUrl);

        Assert.Equal(HardwareProfileTier.HighEnd, plan.RecommendedProfile);
        Assert.Equal(plan.SelectedProfile, plan.RecommendedProfile);
    }
}
