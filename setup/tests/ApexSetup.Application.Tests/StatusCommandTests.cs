using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Pipeline;
using Xunit;

namespace ApexSetup.Application.Tests;

public class StatusCommandTests : IDisposable
{
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-status-dest-").FullName;

    public void Dispose() => Directory.Delete(_destination, recursive: true);

    [Fact]
    public async Task StatusAsync_reports_no_installation_on_an_untouched_destination()
    {
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.StatusAsync(_destination);

        Assert.True(response.Success);
        Assert.Equal("no_installation", response.State);
        Assert.False(response.LockCurrentlyHeld);
    }

    [Fact]
    public async Task StatusAsync_reports_completed_when_a_commit_marker_is_present()
    {
        await File.WriteAllTextAsync(Path.Combine(_destination, ".apex-setup-installed"), "{}");
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.StatusAsync(_destination);

        Assert.True(response.Success);
        Assert.Equal("completed", response.State);
    }

    [Fact]
    public async Task StatusAsync_reports_in_progress_even_before_the_first_stage_has_persisted_any_state()
    {
        // REVUE ADVERSARIALE PHASE 3 : entre InstallationLock.TryAcquire
        // (tout début de InstallationPipeline.RunAsync) et la toute
        // première InstallationStateStore.WriteAsync (après la première
        // étape réussie), le verrou est RÉELLEMENT tenu mais AUCUN état
        // n'est encore persisté — RecoveryClassifier.Classify renvoie donc
        // NoPriorInstallation. Avant correctif, StatusAsync ne sondait le
        // verrou QUE lorsque l'état classifié était déjà
        // InterruptedNeedsRollback — cette fenêtre de démarrage était donc
        // rapportée à tort comme "aucune installation", jamais comme "en
        // cours". Reproductible de façon déterministe (aucun timing) :
        // tenir le verrou soi-même SANS jamais écrire d'état persisté.
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        using (InstallationLock.TryAcquire(_destination))
        {
            var response = await service.StatusAsync(_destination);

            Assert.Equal("in_progress", response.State);
            Assert.True(response.LockCurrentlyHeld);
        }
    }

    [Fact]
    public async Task StatusAsync_distinguishes_in_progress_from_recovery_needed_by_whether_the_lock_is_currently_held()
    {
        await InstallationStateStore.WriteAsync(_destination, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["download"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        // Personne ne tient le verrou : une exécution a été interrompue -> recovery nécessaire.
        var interrupted = await service.StatusAsync(_destination);
        Assert.Equal("recovery_needed", interrupted.State);
        Assert.False(interrupted.LockCurrentlyHeld);

        // Une exécution tient RÉELLEMENT le verrou : ce n'est pas interrompu, c'est en cours.
        using (InstallationLock.TryAcquire(_destination))
        {
            var inProgress = await service.StatusAsync(_destination);
            Assert.Equal("in_progress", inProgress.State);
            Assert.True(inProgress.LockCurrentlyHeld);
        }
    }

    [Fact]
    public async Task StatusAsync_has_no_durable_side_effect_even_when_it_probes_the_lock()
    {
        await InstallationStateStore.WriteAsync(_destination, new InstallationState
        {
            PlanFingerprint = "x", RequiredArtifactIds = [], CompletedStageNames = ["download"],
            Phase = InstallationLifecyclePhase.InProgress, UpdatedAtUtc = "2026-09-04T00:00:00Z",
        });
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        await service.StatusAsync(_destination);

        // La sonde de verrou (acquisition + libération immédiate) ne doit laisser AUCUN fichier de verrou résiduel.
        Assert.False(File.Exists(Path.Combine(_destination, InstallationLock.LockFileName)));
    }

    [Fact]
    public async Task StatusAsync_run_twice_in_a_row_is_idempotent()
    {
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var first = await service.StatusAsync(_destination);
        var second = await service.StatusAsync(_destination);

        Assert.Equal(first.State, second.State);
        Assert.Equal(first.Success, second.Success);
    }
}
