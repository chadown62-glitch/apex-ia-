using ApexSetup.Core.Contracts;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Adversarial;

/// <summary>
/// Revue adversariale §1 (rollback multi-fichiers) et §2 (remplacement de
/// fichier). AVANT le correctif, <c>InstallationStage.RollbackAsync</c>
/// supprimait purement et simplement tout fichier installé — y compris un
/// fichier qui EXISTAIT DÉJÀ avant l'exécution (réinstallation), perdant
/// son contenu d'origine au lieu de le restaurer. Ces tests auraient
/// échoué avant le correctif (fichier absent au lieu de restauré, ou
/// contenu différent de l'original).
/// </summary>
public class RollbackRestorationTests : IDisposable
{
    private readonly string _sourceDir = Directory.CreateTempSubdirectory("apex-setup-rollback-src-").FullName;
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-rollback-dst-").FullName;

    public void Dispose()
    {
        Directory.Delete(_sourceDir, recursive: true);
        Directory.Delete(_destDir, recursive: true);
    }

    private async Task<InstallationContext> BuildContext(params (string id, byte[] newContent)[] artifacts)
    {
        var required = new List<RequiredArtifact>();
        var downloaded = new Dictionary<string, string>();
        foreach (var (id, newContent) in artifacts)
        {
            var sourcePath = Path.Combine(_sourceDir, id);
            await File.WriteAllBytesAsync(sourcePath, newContent);
            required.Add(TestPlans.ArtifactFor(newContent, id));
            downloaded[id] = sourcePath;
        }
        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir, artifacts: required),
            WorkingDirectory = _sourceDir,
            DestinationDirectory = _destDir,
        };
        context.SetItem("downloaded_artifact_paths", downloaded);
        return context;
    }

    /// <summary>Scénario 1 : A -&gt; B -&gt; C -&gt; échec, les trois NOUVEAUX (aucun n'existait avant).</summary>
    [Fact]
    public async Task AllNewFiles_AllRemovedOnRollback()
    {
        var context = await BuildContext(
            ("A", "contenu-A"u8.ToArray()),
            ("B", "contenu-B"u8.ToArray()),
            ("C", "contenu-C"u8.ToArray()));
        var stage = new InstallationStage();

        var outcome = await stage.ExecuteAsync(context);
        Assert.True(outcome.Success);
        var artifactsDir = Path.Combine(_destDir, "artifacts");
        Assert.True(File.Exists(Path.Combine(artifactsDir, "A")));
        Assert.True(File.Exists(Path.Combine(artifactsDir, "B")));
        Assert.True(File.Exists(Path.Combine(artifactsDir, "C")));

        // Simule "puis échec" : rollback déclenché par une étape ultérieure.
        await stage.RollbackAsync(context);

        Assert.False(File.Exists(Path.Combine(artifactsDir, "A")));
        Assert.False(File.Exists(Path.Combine(artifactsDir, "B")));
        Assert.False(File.Exists(Path.Combine(artifactsDir, "C")));
        // Aucune trace de sauvegarde ne doit rester (rien n'avait besoin d'être sauvegardé).
        Assert.Empty(Directory.GetFiles(artifactsDir, "*.installation-backup"));
    }

    /// <summary>Scénario 2 : A -&gt; B -&gt; C -&gt; échec, A et B EXISTAIENT déjà (réinstallation), C nouveau.</summary>
    [Fact]
    public async Task ExistingAAndB_NewC_RollbackRestoresOriginalContentExactly()
    {
        var artifactsDir = Path.Combine(_destDir, "artifacts");
        Directory.CreateDirectory(artifactsDir);
        var originalA = "ORIGINAL-A-content-before-any-install"u8.ToArray();
        var originalB = "ORIGINAL-B-content-before-any-install"u8.ToArray();
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "A"), originalA);
        await File.WriteAllBytesAsync(Path.Combine(artifactsDir, "B"), originalB);
        var originalATime = File.GetLastWriteTimeUtc(Path.Combine(artifactsDir, "A"));

        // Le plan pointe vers un NOUVEAU contenu pour A et B (mise à
        // niveau) et un contenu tout court pour C (nouveau fichier).
        var context = await BuildContext(
            ("A", "NEW-A-content-from-this-install"u8.ToArray()),
            ("B", "NEW-B-content-from-this-install"u8.ToArray()),
            ("C", "NEW-C-content"u8.ToArray()));
        var stage = new InstallationStage();

        var outcome = await stage.ExecuteAsync(context);
        Assert.True(outcome.Success);
        // Vérifie que le remplacement a bien eu lieu avant le rollback.
        Assert.Equal("NEW-A-content-from-this-install"u8.ToArray(), await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "A")));

        await stage.RollbackAsync(context);

        // A et B doivent retrouver EXACTEMENT leur contenu d'origine.
        Assert.True(File.Exists(Path.Combine(artifactsDir, "A")), "A existait avant — doit être RESTAURÉ, pas supprimé");
        Assert.True(File.Exists(Path.Combine(artifactsDir, "B")), "B existait avant — doit être RESTAURÉ, pas supprimé");
        Assert.Equal(originalA, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "A")));
        Assert.Equal(originalB, await File.ReadAllBytesAsync(Path.Combine(artifactsDir, "B")));
        // C était nouveau — doit avoir disparu.
        Assert.False(File.Exists(Path.Combine(artifactsDir, "C")), "C était nouveau — doit être supprimé, pas laissé");
        // Aucun fichier de sauvegarde ne doit rester après restauration.
        Assert.Empty(Directory.GetFiles(artifactsDir, "*.installation-backup"));
    }

    [Fact]
    public async Task Configuration_replacement_then_rollback_restores_the_original_user_configuration()
    {
        var originalConfig = """{"user_customized": true, "note": "ne jamais perdre ceci"}""";
        await File.WriteAllTextAsync(Path.Combine(_destDir, "apex-user-config.json"), originalConfig);

        var context = new InstallationContext
        {
            Plan = TestPlans.MinimalPlan(_destDir),
            WorkingDirectory = _sourceDir,
            DestinationDirectory = _destDir,
        };
        var stage = new ConfigurationStage();

        var outcome = await stage.ExecuteAsync(context);
        Assert.True(outcome.Success);
        Assert.NotEqual(originalConfig, await File.ReadAllTextAsync(Path.Combine(_destDir, "apex-user-config.json")));

        await stage.RollbackAsync(context);

        Assert.Equal(originalConfig, await File.ReadAllTextAsync(Path.Combine(_destDir, "apex-user-config.json")));
        Assert.False(File.Exists(Path.Combine(_destDir, "apex-user-config.json.installation-backup")));
    }
}
