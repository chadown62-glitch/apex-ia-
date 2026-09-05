using ApexSetup.Core.Pipeline;
using Xunit;

namespace ApexSetup.Core.Tests.Pipeline;

/// <summary>Les 4 états explicitement exigés par la revue adversariale (§4 Recovery).</summary>
public class RecoveryClassifierTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-recovery-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    [Fact]
    public void Empty_destination_is_NoPriorInstallation()
    {
        var assessment = RecoveryClassifier.Classify(_tempDir);
        Assert.Equal(RecoveryState.NoPriorInstallation, assessment.State);
    }

    [Fact]
    public async Task Commit_marker_with_committed_state_is_Completed()
    {
        await File.WriteAllTextAsync(Path.Combine(_tempDir, ".apex-setup-installed"), "{}");
        await InstallationStateStore.WriteAsync(_tempDir, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["commit"],
            Phase = InstallationLifecyclePhase.Committed, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.Completed, assessment.State);
    }

    [Fact]
    public async Task Commit_marker_alone_without_any_state_file_is_Completed()
    {
        // Compatibilité : une installation antérieure à l'introduction de
        // InstallationState n'a laissé qu'un marqueur — doit rester
        // reconnue comme complète, pas comme corrompue.
        await File.WriteAllTextAsync(Path.Combine(_tempDir, ".apex-setup-installed"), "{}");

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.Completed, assessment.State);
    }

    [Fact]
    public async Task InProgress_state_without_commit_marker_is_InterruptedNeedsRollback()
    {
        await InstallationStateStore.WriteAsync(_tempDir, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = ["model-a"], CompletedStageNames = ["download", "installation"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.InterruptedNeedsRollback, assessment.State);
        Assert.NotNull(assessment.PersistedState);
        Assert.Equal(["download", "installation"], assessment.PersistedState!.CompletedStageNames);
    }

    [Fact]
    public void Unreadable_state_file_is_Corrupt()
    {
        File.WriteAllText(Path.Combine(_tempDir, InstallationStateStore.FileName), "{{{ pas du JSON");

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.Corrupt, assessment.State);
    }

    [Fact]
    public async Task Commit_marker_with_InProgress_state_is_Corrupt_an_inconsistency_never_guessed_away()
    {
        await File.WriteAllTextAsync(Path.Combine(_tempDir, ".apex-setup-installed"), "{}");
        await InstallationStateStore.WriteAsync(_tempDir, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["download"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.Corrupt, assessment.State);
    }

    [Fact]
    public async Task Committed_state_without_a_commit_marker_is_Corrupt()
    {
        await InstallationStateStore.WriteAsync(_tempDir, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["commit"],
            Phase = InstallationLifecyclePhase.Committed, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });

        var assessment = RecoveryClassifier.Classify(_tempDir);

        Assert.Equal(RecoveryState.Corrupt, assessment.State);
    }
}
