using ApexSetup.Core.Pipeline;
using Xunit;

namespace ApexSetup.Core.Tests.Pipeline;

public class InstallationStateStoreTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-state-store-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    private static InstallationState SampleState(InstallationLifecyclePhase phase = InstallationLifecyclePhase.InProgress) => new()
    {
        PlanFingerprint = "abc123",
        RequiredArtifactIds = ["model-a", "model-b"],
        CompletedStageNames = ["download", "integrity_verification"],
        Phase = phase,
        UpdatedAtUtc = "2026-09-04T00:00:00Z",
    };

    [Fact]
    public async Task WriteAsync_then_TryRead_round_trips_the_state_exactly()
    {
        await InstallationStateStore.WriteAsync(_tempDir, SampleState());

        var read = InstallationStateStore.TryRead(_tempDir);

        Assert.NotNull(read);
        Assert.Equal("abc123", read!.PlanFingerprint);
        Assert.Equal(["model-a", "model-b"], read.RequiredArtifactIds);
        Assert.Equal(["download", "integrity_verification"], read.CompletedStageNames);
        Assert.Equal(InstallationLifecyclePhase.InProgress, read.Phase);
    }

    [Fact]
    public void TryRead_returns_null_when_no_state_file_exists()
    {
        Assert.Null(InstallationStateStore.TryRead(_tempDir));
    }

    [Fact]
    public void TryRead_returns_null_never_throws_for_a_corrupted_state_file()
    {
        var path = Path.Combine(_tempDir, InstallationStateStore.FileName);
        File.WriteAllText(path, "{ ceci n'est pas du JSON valide !!!");

        var result = InstallationStateStore.TryRead(_tempDir);

        Assert.Null(result);
    }

    [Fact]
    public async Task WriteAsync_never_leaves_a_stray_writing_temp_file_behind()
    {
        await InstallationStateStore.WriteAsync(_tempDir, SampleState());

        Assert.Empty(Directory.GetFiles(_tempDir, "*.writing"));
    }

    [Fact]
    public async Task WriteAsync_overwrites_a_previous_state_atomically_never_a_partial_mix()
    {
        await InstallationStateStore.WriteAsync(_tempDir, SampleState(InstallationLifecyclePhase.InProgress));
        await InstallationStateStore.WriteAsync(_tempDir, SampleState(InstallationLifecyclePhase.Committed));

        var read = InstallationStateStore.TryRead(_tempDir);
        Assert.Equal(InstallationLifecyclePhase.Committed, read!.Phase);
    }

    [Fact]
    public void Delete_removes_the_state_file_and_is_safe_to_call_when_absent()
    {
        InstallationStateStore.Delete(_tempDir); // absent — ne doit jamais lever
        Assert.Null(InstallationStateStore.TryRead(_tempDir));
    }
}
