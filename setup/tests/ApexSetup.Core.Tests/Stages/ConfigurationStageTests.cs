using System.Text.Json;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class ConfigurationStageTests : IDisposable
{
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-config-stage-").FullName;

    public void Dispose() => Directory.Delete(_destDir, recursive: true);

    private InstallationContext MakeContext() => new()
    {
        Plan = TestPlans.MinimalPlan(_destDir, artifacts: [TestPlans.ArtifactFor("x"u8.ToArray(), "model-a")]),
        WorkingDirectory = _destDir,
        DestinationDirectory = _destDir,
    };

    [Fact]
    public async Task ExecuteAsync_writes_a_readable_json_configuration_file_derived_from_the_plan()
    {
        var context = MakeContext();

        var outcome = await new ConfigurationStage().ExecuteAsync(context);

        Assert.True(outcome.Success);
        var configPath = Path.Combine(_destDir, "apex-user-config.json");
        Assert.True(File.Exists(configPath));
        using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(configPath));
        Assert.Equal(context.Plan.TargetApexVersion, doc.RootElement.GetProperty("ApexVersion").GetString());
        Assert.Equal(context.Plan.SelectedProfile.ToString(), doc.RootElement.GetProperty("Profile").GetString());
    }

    [Fact]
    public async Task RollbackAsync_deletes_the_configuration_file()
    {
        var context = MakeContext();
        await new ConfigurationStage().ExecuteAsync(context);
        var configPath = Path.Combine(_destDir, "apex-user-config.json");
        Assert.True(File.Exists(configPath));

        await new ConfigurationStage().RollbackAsync(context);

        Assert.False(File.Exists(configPath));
    }
}
