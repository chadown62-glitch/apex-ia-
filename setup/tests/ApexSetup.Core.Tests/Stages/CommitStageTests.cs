using System.Text.Json;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Stages;

public class CommitStageTests : IDisposable
{
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-commit-stage-").FullName;

    public void Dispose() => Directory.Delete(_destDir, recursive: true);

    private InstallationContext MakeContext() => new()
    {
        Plan = TestPlans.MinimalPlan(_destDir),
        WorkingDirectory = _destDir,
        DestinationDirectory = _destDir,
    };

    [Fact]
    public async Task ExecuteAsync_writes_a_commit_marker_recording_the_installed_version_and_profile()
    {
        var context = MakeContext();

        var outcome = await new CommitStage().ExecuteAsync(context);

        Assert.True(outcome.Success);
        var markerPath = Path.Combine(_destDir, ".apex-setup-installed");
        Assert.True(File.Exists(markerPath));
        using var doc = JsonDocument.Parse(await File.ReadAllTextAsync(markerPath));
        Assert.Equal(context.Plan.TargetApexVersion, doc.RootElement.GetProperty("ApexVersion").GetString());
        Assert.True(DateTimeOffset.TryParse(doc.RootElement.GetProperty("CommittedAtUtc").GetString(), out _));
    }

    [Fact]
    public async Task RollbackAsync_never_removes_the_commit_marker_point_of_no_return()
    {
        var context = MakeContext();
        await new CommitStage().ExecuteAsync(context);
        var markerPath = Path.Combine(_destDir, ".apex-setup-installed");
        Assert.True(File.Exists(markerPath));

        await new CommitStage().RollbackAsync(context);

        Assert.True(File.Exists(markerPath), "Commit est le point de non-retour — son rollback ne doit jamais défaire le marqueur écrit.");
    }
}
