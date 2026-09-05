using ApexSetup.Core.Contracts;
using ApexSetup.Core.Download;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Adversarial;

/// <summary>
/// Revue adversariale §3 (crash safety) + §4 (recovery) + §14 (état
/// transactionnel), bout en bout. AVANT le correctif, un processus tué
/// après Installation mais avant Commit ne laissait AUCUNE trace
/// exploitable — un fichier "A" préexistant écrasé par la nouvelle
/// installation restait écrasé pour toujours, aucun outil ne pouvait
/// détecter ni réparer la situation. Simule un « crash » en exécutant
/// délibérément un pipeline TRONQUÉ (s'arrête après Installation, jamais
/// Commit) — exactement l'état sur disque qu'un vrai kill -9 laisserait,
/// puisque l'écriture de l'état se fait après CHAQUE étape réussie.
/// </summary>
public class CrashRecoveryTests : IDisposable
{
    private readonly string _workingDir = Directory.CreateTempSubdirectory("apex-setup-crash-work-").FullName;
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-crash-dest-").FullName;

    public void Dispose()
    {
        Directory.Delete(_workingDir, recursive: true);
        Directory.Delete(_destDir, recursive: true);
    }

    [Fact]
    public async Task Recovery_correctly_classifies_and_repairs_a_process_killed_after_installation_but_before_commit()
    {
        // --- Installation légitime précédente : "A" existe déjà. ---
        var artifactsDir = Path.Combine(_destDir, "artifacts");
        Directory.CreateDirectory(artifactsDir);
        var originalA = "ORIGINAL-A-du-vrai-utilisateur"u8.ToArray();
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "A"), originalA);

        var newA = "NOUVELLE-VERSION-DE-A"u8.ToArray();
        var newB = "B-tout-nouveau"u8.ToArray();
        var plan = TestPlans.MinimalPlan(_destDir, artifacts:
        [
            TestPlans.ArtifactFor(newA, "A"),
            TestPlans.ArtifactFor(newB, "B"),
        ]);

        await File.WriteAllBytesAsync(Path.Combine(_workingDir, "A"), newA);
        await File.WriteAllBytesAsync(Path.Combine(_workingDir, "B"), newB);

        // --- Pipeline TRONQUÉ : jamais Configuration/Validation/Commit —
        // reproduit exactement l'état laissé par un `kill -9` juste après
        // Installation. ---
        var truncatedPipeline = new InstallationPipeline([
            new FakeDownloadPassthroughStage(),
            new InstallationStage(),
        ]);
        var context = new InstallationContext { Plan = plan, WorkingDirectory = _workingDir, DestinationDirectory = _destDir };

        var result = await truncatedPipeline.RunAsync(context);
        Assert.True(result.Success); // le pipeline tronqué lui-même réussit — le "crash" survient APRÈS, hors de son contrôle

        // À ce stade : A a été écrasé, B créé, AUCUN commit n'a eu lieu.
        Assert.Equal(newA, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "A")));
        Assert.True(File.Exists(Path.Combine(artifactsDir, "B")));
        Assert.False(File.Exists(Path.Combine(_destDir, ".apex-setup-installed")));

        // --- "Redémarrage de Setup" : classification avant toute action. ---
        var assessment = RecoveryClassifier.Classify(_destDir);
        Assert.Equal(RecoveryState.InterruptedNeedsRollback, assessment.State);
        Assert.Equal(["download", "installation"], assessment.PersistedState!.CompletedStageNames);

        // --- Récupération réelle. ---
        InstallationRecovery.RollbackInterrupted(_destDir);

        // A doit avoir retrouvé EXACTEMENT son contenu original.
        Assert.Equal(originalA, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "A")));
        // B, entièrement nouveau, doit avoir disparu.
        Assert.False(File.Exists(Path.Combine(artifactsDir, "B")));
        // L'état persisté est nettoyé — une nouvelle classification doit repartir de zéro.
        Assert.Equal(RecoveryState.NoPriorInstallation, RecoveryClassifier.Classify(_destDir).State);
    }

    [Fact]
    public void RollbackInterrupted_throws_rather_than_silently_no_opping_when_called_on_a_completed_installation()
    {
        Directory.CreateDirectory(_destDir);
        File.WriteAllText(Path.Combine(_destDir, ".apex-setup-installed"), "{}");

        Assert.Throws<InvalidOperationException>(() => InstallationRecovery.RollbackInterrupted(_destDir));
    }

    [Fact]
    public void RollbackInterrupted_throws_when_there_is_nothing_to_recover()
    {
        Assert.Throws<InvalidOperationException>(() => InstallationRecovery.RollbackInterrupted(_destDir));
    }

    /// <summary>Double minimal qui fait passer les artefacts déjà présents dans WorkingDirectory comme "téléchargés", sans réseau — isole le scénario sur Installation/Recovery.</summary>
    private sealed class FakeDownloadPassthroughStage : IInstallationStage
    {
        public string Name => "download";

        public Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
        {
            var paths = context.Plan.RequiredArtifacts.ToDictionary(
                a => a.ArtifactId, a => Path.Combine(context.WorkingDirectory, a.ArtifactId));
            context.SetItem("downloaded_artifact_paths", paths);
            return Task.FromResult(StageOutcome.Ok());
        }

        public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
    }
}
