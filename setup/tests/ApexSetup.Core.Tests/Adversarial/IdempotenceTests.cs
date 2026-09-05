using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Adversarial;

/// <summary>
/// Revue adversariale §10 (idempotence). AVANT le correctif,
/// <c>DownloadStage</c> déclenchait systématiquement une requête réseau
/// pour chaque artefact, même déjà présent et valide sur disque — ce test
/// aurait échoué (URL redemandée deux fois) sans le correctif.
/// </summary>
public class IdempotenceTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-idempotence-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    [Fact]
    public async Task DownloadStage_never_calls_the_network_again_for_an_artifact_already_valid_on_disk()
    {
        var content = "contenu déjà téléchargé et valide"u8.ToArray();
        var artifact = TestPlans.ArtifactFor(content, "already-valid");
        var finalPath = Path.Combine(_tempDir, "already-valid");
        await File.WriteAllBytesAsync(finalPath, content);

        var downloader = new FakeArtifactDownloader(); // aucune réponse enregistrée -> échouerait si sollicité
        var stage = new DownloadStage(downloader);
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_tempDir, artifacts: [artifact]),
            WorkingDirectory = _tempDir,
            DestinationDirectory = _tempDir,
        };

        var outcome = await stage.ExecuteAsync(context);

        Assert.True(outcome.Success, outcome.ErrorMessage);
        Assert.Empty(downloader.RequestedUrls); // preuve directe : le réseau n'a jamais été sollicité
    }

    [Fact]
    public async Task DownloadStage_redownloads_when_the_file_on_disk_exists_but_does_not_match_the_expected_hash()
    {
        var realContent = "vrai contenu"u8.ToArray();
        var artifact = TestPlans.ArtifactFor(realContent, "stale-cache");
        var finalPath = Path.Combine(_tempDir, "stale-cache");
        await File.WriteAllBytesAsync(finalPath, "contenu périmé/corrompu d'une exécution précédente"u8.ToArray());

        var downloader = new FakeArtifactDownloader().Enqueue(
            artifact.SourceUrl, ApexSetup.Core.Download.ArtifactDownloadResult.Success(finalPath, artifact.Sha256));
        var stage = new DownloadStage(downloader);
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_tempDir, artifacts: [artifact]),
            WorkingDirectory = _tempDir,
            DestinationDirectory = _tempDir,
        };

        var outcome = await stage.ExecuteAsync(context);

        Assert.True(outcome.Success, outcome.ErrorMessage);
        Assert.Single(downloader.RequestedUrls); // le réseau EST sollicité, car le cache était invalide
    }

    [Fact]
    public async Task Running_the_same_installation_stage_twice_in_a_row_yields_byte_identical_output_and_no_corruption()
    {
        var content = "poids de modèle stable"u8.ToArray();
        var sourcePath = Path.Combine(_tempDir, "source-model");
        await File.WriteAllBytesAsync(sourcePath, content);
        var artifact = TestPlans.ArtifactFor(content, "model");
        var destDir = Directory.CreateTempSubdirectory("apex-setup-idempotence-dest-").FullName;
        try
        {
            var context = new InstallationContext
            {
                Plan = TestPlans.MinimalPlan(destDir, artifacts: [artifact]),
                WorkingDirectory = _tempDir,
                DestinationDirectory = destDir,
            };
            context.SetItem("downloaded_artifact_paths", new Dictionary<string, string> { ["model"] = sourcePath });

            var first = await new InstallationStage().ExecuteAsync(context);
            Assert.True(first.Success);
            var firstBytes = await File.ReadAllBytesAsync(Path.Combine(destDir, "artifacts", "model"));

            var context2 = new InstallationContext
            {
                Plan = TestPlans.MinimalPlan(destDir, artifacts: [artifact]),
                WorkingDirectory = _tempDir,
                DestinationDirectory = destDir,
            };
            context2.SetItem("downloaded_artifact_paths", new Dictionary<string, string> { ["model"] = sourcePath });
            var second = await new InstallationStage().ExecuteAsync(context2);

            Assert.True(second.Success);
            var secondBytes = await File.ReadAllBytesAsync(Path.Combine(destDir, "artifacts", "model"));
            Assert.Equal(firstBytes, secondBytes);
            Assert.Equal(content, secondBytes);
            // Pas de sauvegarde qui traînerait après deux exécutions réussies consécutives.
            Assert.Empty(Directory.GetFiles(Path.Combine(destDir, "artifacts"), "*.installation-backup"));
        }
        finally
        {
            Directory.Delete(destDir, recursive: true);
        }
    }
}
