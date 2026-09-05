using ApexSetup.Core.Contracts;
using ApexSetup.Core.Integrity;

namespace ApexSetup.Core.Tests.Fixtures;

internal static class TestPlans {
    public static InstallationPlan MinimalPlan(
        string destination,
        IReadOnlyList<RequiredArtifact>? artifacts = null,
        IReadOnlyList<ModelManifestEntry>? models = null) => new()
    {
        TargetApexVersion = "0.1.2",
        SelectedProfile = HardwareProfileTier.Standard,
        RequiredArtifacts = artifacts ?? [],
        RequiredModels = models ?? [],
        Destination = destination,
        RequiredDiskBytes = 0,
        Steps = [],
        Dependencies = new Dictionary<string, DependencyRequirement>(),
    };

    public static RequiredArtifact ArtifactFor(byte[] content, string artifactId) => new()
    {
        ArtifactId = artifactId,
        SourceUrl = $"http://unused.invalid/{artifactId}",
        SizeBytes = content.Length,
        Sha256 = Sha256Verifier.ComputeSha256(content),
    };
}
