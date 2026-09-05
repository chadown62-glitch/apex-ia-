using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Pipeline;

public class InstallationPipelineTests
{
    private static InstallationContext MakeContext() => new()
    {
        Plan = TestPlans.MinimalPlan(Path.GetTempPath()),
        WorkingDirectory = Path.GetTempPath(),
        DestinationDirectory = Path.GetTempPath(),
    };

    [Fact]
    public async Task RunAsync_executes_every_stage_in_declared_order_when_all_succeed()
    {
        var a = new FakeInstallationStage("a");
        var b = new FakeInstallationStage("b");
        var c = new FakeInstallationStage("c");
        var pipeline = new InstallationPipeline([a, b, c]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.True(result.Success);
        Assert.Equal(["a", "b", "c"], result.ExecutedStageNames);
        Assert.True(a.ExecuteCalled);
        Assert.True(b.ExecuteCalled);
        Assert.True(c.ExecuteCalled);
        Assert.False(a.RollbackCalled);
        Assert.False(b.RollbackCalled);
        Assert.False(c.RollbackCalled);
    }

    [Fact]
    public async Task RunAsync_rolls_back_completed_stages_in_reverse_order_on_failure()
    {
        var a = new FakeInstallationStage("a");
        var b = new FakeInstallationStage("b");
        var failing = new FakeInstallationStage("failing", shouldSucceed: false);
        var neverReached = new FakeInstallationStage("never_reached");
        var pipeline = new InstallationPipeline([a, b, failing, neverReached]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.False(result.Success);
        Assert.Equal("failing", result.FailedStageName);
        Assert.False(neverReached.ExecuteCalled);
        Assert.False(neverReached.RollbackCalled); // jamais exécutée -> jamais annulée
        Assert.False(failing.RollbackCalled); // l'étape en échec elle-même n'est jamais "annulée"
        Assert.True(a.RollbackCalled);
        Assert.True(b.RollbackCalled);
        // Ordre INVERSE : b avant a.
        Assert.Equal(["b", "a"], result.RolledBackStageNames);
    }

    [Fact]
    public async Task RunAsync_continues_rolling_back_earlier_stages_even_if_a_later_rollback_throws()
    {
        var a = new FakeInstallationStage("a");
        var brokenRollback = new FakeInstallationStage("broken_rollback", rollbackThrows: true);
        var failing = new FakeInstallationStage("failing", shouldSucceed: false);
        var pipeline = new InstallationPipeline([a, brokenRollback, failing]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.False(result.Success);
        Assert.True(brokenRollback.RollbackCalled);
        Assert.True(a.RollbackCalled); // jamais bloqué par l'échec du rollback précédent
        Assert.Contains("a", result.RolledBackStageNames);
        Assert.DoesNotContain("broken_rollback", result.RolledBackStageNames); // son propre rollback a échoué
    }

    [Fact]
    public async Task RunAsync_explicitly_reports_a_failed_rollback_never_leaving_it_to_be_inferred(
        )
    {
        // Revue adversariale §13 : un rollback en échec doit être SIGNALÉ,
        // jamais seulement déductible en comparant deux listes.
        var brokenRollback = new FakeInstallationStage("broken_rollback", rollbackThrows: true);
        var failing = new FakeInstallationStage("failing", shouldSucceed: false);
        var pipeline = new InstallationPipeline([brokenRollback, failing]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.Contains("broken_rollback", result.FailedRollbackStageNames);
    }

    [Fact]
    public async Task RunAsync_succeeded_result_always_carries_empty_not_null_rollback_lists()
    {
        var pipeline = new InstallationPipeline([new FakeInstallationStage("a")]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.True(result.Success);
        Assert.NotNull(result.RolledBackStageNames);
        Assert.NotNull(result.FailedRollbackStageNames);
        Assert.Empty(result.FailedRollbackStageNames);
    }

    [Fact]
    public async Task RunAsync_treats_an_unhandled_exception_from_a_stage_as_a_failure_not_a_crash()
    {
        var throwing = new ThrowingStage();
        var pipeline = new InstallationPipeline([throwing]);

        var result = await pipeline.RunAsync(MakeContext());

        Assert.False(result.Success);
        Assert.Equal("throwing", result.FailedStageName);
        Assert.Contains("InvalidOperationException", result.FailureDetail);
    }

    [Fact]
    public async Task RunAsync_never_calls_rollback_on_a_stage_that_never_executed()
    {
        var failingFirst = new FakeInstallationStage("failing_first", shouldSucceed: false);
        var second = new FakeInstallationStage("second");
        var pipeline = new InstallationPipeline([failingFirst, second]);

        await pipeline.RunAsync(MakeContext());

        Assert.False(second.ExecuteCalled);
        Assert.False(second.RollbackCalled);
    }

    private sealed class ThrowingStage : IInstallationStage
    {
        public string Name => "throwing";
        public Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("boum");
        public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
    }
}
