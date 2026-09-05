using ApexSetup.Core.Contracts;
using ApexSetup.Core.Hardware;
using ApexSetup.Core.Planning;
using Xunit;

namespace ApexSetup.Core.Tests.Planning;

public class PlanFingerprintTests
{
    private static InstallationPlan MakePlan(params RequiredArtifact[] artifacts) => new()
    {
        TargetApexVersion = "0.1.2", SelectedProfile = HardwareProfileTier.Standard,
        RequiredArtifacts = artifacts, RequiredModels = [], Destination = "/dest", RequiredDiskBytes = 0,
        Steps = [], Dependencies = new Dictionary<string, DependencyRequirement>(),
    };

    private static RequiredArtifact Artifact(string id, string sha) => new()
    {
        ArtifactId = id, SourceUrl = $"http://x/{id}", SizeBytes = 1, Sha256 = sha,
    };

    [Fact]
    public void Compute_is_identical_for_the_same_plan_content_regardless_of_artifact_order()
    {
        var planA = MakePlan(Artifact("a", new string('1', 64)), Artifact("b", new string('2', 64)));
        var planB = MakePlan(Artifact("b", new string('2', 64)), Artifact("a", new string('1', 64)));

        Assert.Equal(PlanFingerprint.Compute(planA), PlanFingerprint.Compute(planB));
    }

    [Fact]
    public void Compute_differs_when_a_single_artifact_hash_changes()
    {
        var planA = MakePlan(Artifact("a", new string('1', 64)));
        var planB = MakePlan(Artifact("a", new string('9', 64)));

        Assert.NotEqual(PlanFingerprint.Compute(planA), PlanFingerprint.Compute(planB));
    }

    [Fact]
    public void Compute_differs_when_the_destination_changes()
    {
        var planA = MakePlan() with { Destination = "/dest-a" };
        var planB = MakePlan() with { Destination = "/dest-b" };

        Assert.NotEqual(PlanFingerprint.Compute(planA), PlanFingerprint.Compute(planB));
    }
}
