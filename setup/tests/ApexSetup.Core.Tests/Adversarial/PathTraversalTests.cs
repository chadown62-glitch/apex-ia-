using ApexSetup.Core.Contracts;
using ApexSetup.Core.Download;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Adversarial;

/// <summary>
/// Revue adversariale §6 (Path Security) : preuve bout-en-bout qu'un
/// manifeste malveillant (<c>ArtifactId</c> contenant une traversée ou un
/// chemin absolu) ne peut jamais faire écrire un fichier hors des
/// répertoires prévus. Avant le correctif (<c>Security/PathSafety</c>),
/// <c>Path.Combine(workingDir, "../../../tmp/evil")</c> produisait
/// silencieusement un chemin hors de <c>workingDir</c> ; ce test aurait
/// échoué (fichier créé hors cible) sans la correction.
/// </summary>
public class PathTraversalTests : IDisposable
{
    private readonly string _workingDir = Directory.CreateTempSubdirectory("apex-setup-traversal-work-").FullName;
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-traversal-dest-").FullName;
    private readonly string _outsideMarker;

    public PathTraversalTests()
    {
        // Un répertoire "sensible" en dehors de tout périmètre légitime —
        // si un fichier y apparaît, la traversée a réussi.
        _outsideMarker = Path.Combine(Path.GetTempPath(), $"apex-setup-OUTSIDE-{Guid.NewGuid():N}");
        Directory.CreateDirectory(_outsideMarker);
    }

    public void Dispose()
    {
        Directory.Delete(_workingDir, recursive: true);
        Directory.Delete(_destDir, recursive: true);
        Directory.Delete(_outsideMarker, recursive: true);
    }

    [Theory]
    [InlineData("../../../../../../tmp/evil-traversal")]
    [InlineData("../evil-sibling")]
    public async Task DownloadStage_refuses_a_traversal_artifact_id_instead_of_writing_outside_working_directory(string maliciousId)
    {
        var artifact = new RequiredArtifact
        {
            ArtifactId = maliciousId, SourceUrl = "http://unused.invalid/x", SizeBytes = 1, Sha256 = new string('a', 64),
        };
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir, artifacts: [artifact]),
            WorkingDirectory = _workingDir,
            DestinationDirectory = _destDir,
        };
        var stage = new DownloadStage(new FakeArtifactDownloader());

        var outcome = await stage.ExecuteAsync(context);

        Assert.False(outcome.Success);
        AssertNothingEscaped();
    }

    [Fact]
    public async Task DownloadStage_refuses_an_absolute_path_artifact_id()
    {
        var artifact = new RequiredArtifact
        {
            ArtifactId = "/etc/passwd", SourceUrl = "http://unused.invalid/x", SizeBytes = 1, Sha256 = new string('a', 64),
        };
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir, artifacts: [artifact]),
            WorkingDirectory = _workingDir,
            DestinationDirectory = _destDir,
        };
        var stage = new DownloadStage(new FakeArtifactDownloader());

        var outcome = await stage.ExecuteAsync(context);

        Assert.False(outcome.Success);
        Assert.False(File.Exists("/etc/passwd-should-never-be-touched"));
    }

    [Theory]
    [InlineData("../../../../../../tmp/evil-install")]
    [InlineData("..\\..\\evil-windows-style")]
    public async Task InstallationStage_refuses_a_traversal_artifact_id_instead_of_writing_outside_the_artifacts_directory(string maliciousId)
    {
        // Simule un artefact déjà "téléchargé" (contourne Download pour
        // isoler spécifiquement la défense d'InstallationStage).
        var legitimateSource = Path.Combine(_workingDir, "harmless-source");
        await File.WriteAllTextAsync(legitimateSource, "contenu");

        var artifact = new RequiredArtifact
        {
            ArtifactId = maliciousId, SourceUrl = "http://unused.invalid/x", SizeBytes = 1, Sha256 = new string('a', 64),
        };
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir, artifacts: [artifact]),
            WorkingDirectory = _workingDir,
            DestinationDirectory = _destDir,
        };
        context.SetItem("downloaded_artifact_paths", new Dictionary<string, string> { [maliciousId] = legitimateSource });

        var outcome = await new InstallationStage().ExecuteAsync(context);

        Assert.False(outcome.Success);
        AssertNothingEscaped();
    }

    private void AssertNothingEscaped()
    {
        Assert.Empty(Directory.GetFiles(_outsideMarker));
        // Rien ne doit non plus apparaître au-dessus de /tmp/apex-setup-*
        // (preuve négative supplémentaire : aucun fichier "evil*" n'existe
        // dans /tmp lui-même, hors des répertoires de test légitimes).
        var strayFiles = Directory.GetFiles(Path.GetTempPath())
            .Where(f => Path.GetFileName(f).Contains("evil", StringComparison.OrdinalIgnoreCase));
        Assert.Empty(strayFiles);
    }
}
