using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class InstallationStageTests : IDisposable
{
    private readonly string _sourceDir = Directory.CreateTempSubdirectory("apex-setup-install-src-").FullName;
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-install-dst-").FullName;

    public void Dispose()
    {
        Directory.Delete(_sourceDir, recursive: true);
        Directory.Delete(_destDir, recursive: true);
    }

    private InstallationContext MakeContext(params (string id, byte[] content)[] artifacts)
    {
        var required = new List<ApexSetup.Core.Contracts.RequiredArtifact>();
        var downloaded = new Dictionary<string, string>();
        foreach (var (id, content) in artifacts)
        {
            var path = Path.Combine(_sourceDir, id);
            File.WriteAllBytes(path, content);
            required.Add(TestPlans.ArtifactFor(content, id));
            downloaded[id] = path;
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

    [Fact]
    public async Task ExecuteAsync_copies_every_artifact_into_destination_artifacts_directory_with_identical_bytes()
    {
        var content = "poids de modèle réel"u8.ToArray();
        var context = MakeContext(("model-a", content));

        var outcome = await new InstallationStage().ExecuteAsync(context);

        Assert.True(outcome.Success);
        var installedPath = Path.Combine(_destDir, "artifacts", "model-a");
        Assert.True(File.Exists(installedPath));
        Assert.Equal(content, await File.ReadAllBytesAsync(installedPath));
    }

    [Fact]
    public async Task ExecuteAsync_leaves_the_source_file_in_the_working_directory_untouched()
    {
        var content = "cache réutilisable"u8.ToArray();
        var context = MakeContext(("model-a", content));
        var sourcePath = Path.Combine(_sourceDir, "model-a");

        await new InstallationStage().ExecuteAsync(context);

        Assert.True(File.Exists(sourcePath)); // copie, jamais un déplacement
    }

    [Fact]
    public async Task RollbackAsync_removes_every_installed_file_but_never_touches_the_working_directory_cache()
    {
        var content = "x"u8.ToArray();
        var context = MakeContext(("model-a", content));
        await new InstallationStage().ExecuteAsync(context);
        var installedPath = Path.Combine(_destDir, "artifacts", "model-a");
        var sourcePath = Path.Combine(_sourceDir, "model-a");
        Assert.True(File.Exists(installedPath));

        await new InstallationStage().RollbackAsync(context);

        Assert.False(File.Exists(installedPath));
        Assert.True(File.Exists(sourcePath));
    }
}
