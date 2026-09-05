using ApexSetup.Core.Contracts;
using ApexSetup.Core.Download;
using ApexSetup.Core.Pipeline;
using ApexSetup.Core.Stages;
using ApexSetup.Core.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Core.Tests.Adversarial;

/// <summary>
/// Revue adversariale §7 (Concurrence). AVANT le correctif, rien
/// n'empêchait deux exécutions du pipeline de cibler la même destination
/// — ce test aurait laissé les deux pipelines s'exécuter simultanément
/// sans le verrou.
/// </summary>
public class ConcurrencyTests : IDisposable
{
    private readonly string _destDir = Directory.CreateTempSubdirectory("apex-setup-concurrency-dest-").FullName;
    private readonly string _workDirA = Directory.CreateTempSubdirectory("apex-setup-concurrency-work-a-").FullName;
    private readonly string _workDirB = Directory.CreateTempSubdirectory("apex-setup-concurrency-work-b-").FullName;

    public void Dispose()
    {
        Directory.Delete(_destDir, recursive: true);
        Directory.Delete(_workDirA, recursive: true);
        Directory.Delete(_workDirB, recursive: true);
    }

    private sealed class SlowStage(TaskCompletionSource releaseGate) : IInstallationStage
    {
        public string Name => "slow";
        public async Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
        {
            await releaseGate.Task; // reste "en cours" jusqu'à ce que le test le libère
            return StageOutcome.Ok();
        }
        public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default) => Task.CompletedTask;
    }

    [Fact]
    public async Task Second_pipeline_run_against_the_same_destination_is_explicitly_refused_while_the_first_is_in_progress()
    {
        var releaseGate = new TaskCompletionSource();
        var slowPipeline = new InstallationPipeline([new SlowStage(releaseGate)]);
        var plan = TestPlans.MinimalPlan(_destDir);

        var firstRunTask = slowPipeline.RunAsync(new InstallationContext
        {
            Plan = plan, WorkingDirectory = _workDirA, DestinationDirectory = _destDir,
        });

        // Laisse la première exécution acquérir réellement le verrou avant de tenter la seconde.
        await WaitUntil(() => File.Exists(Path.Combine(_destDir, InstallationLock.LockFileName)));

        var secondPipeline = new InstallationPipeline([new SlowStage(new TaskCompletionSource())]);
        var secondResult = await secondPipeline.RunAsync(new InstallationContext
        {
            Plan = plan, WorkingDirectory = _workDirB, DestinationDirectory = _destDir,
        });

        Assert.False(secondResult.Success);
        Assert.Contains("déjà en cours", secondResult.FailureDetail);
        Assert.Empty(secondResult.ExecutedStageNames); // refusé AVANT toute étape — jamais un échec de la 1ère étape

        releaseGate.SetResult();
        var firstResult = await firstRunTask;
        Assert.True(firstResult.Success);
    }

    [Fact]
    public async Task A_new_pipeline_run_succeeds_once_a_previous_run_against_the_same_destination_has_finished()
    {
        var plan = TestPlans.MinimalPlan(_destDir);
        var firstPipeline = new InstallationPipeline([new FakeInstallationStage("only")]);
        var firstResult = await firstPipeline.RunAsync(new InstallationContext
        {
            Plan = plan, WorkingDirectory = _workDirA, DestinationDirectory = _destDir,
        });
        Assert.True(firstResult.Success);

        var secondPipeline = new InstallationPipeline([new FakeInstallationStage("only")]);
        var secondResult = await secondPipeline.RunAsync(new InstallationContext
        {
            Plan = plan, WorkingDirectory = _workDirB, DestinationDirectory = _destDir,
        });

        Assert.True(secondResult.Success);
    }

    private static async Task WaitUntil(Func<bool> condition, int timeoutMs = 5000)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException("condition jamais atteinte");
            }
            await Task.Delay(10);
        }
    }
}
