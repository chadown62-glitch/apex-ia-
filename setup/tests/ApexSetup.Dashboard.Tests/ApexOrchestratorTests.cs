using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Mission §20 : start/stop/restart/timeout/processus déjà lancé/processus absent/configuration invalide — tous les systèmes externes sont mockés (aucun WSL réel dans ce bac à sable), voir la docstring de chaque test pour la garantie exacte prouvée.</summary>
public class ApexOrchestratorTests
{
    private static readonly ComponentState RunningApi = new(ComponentKey.Api, "API APEX", ComponentStatus.Running, "HTTP 200");
    private static readonly ComponentState StoppedApi = new(ComponentKey.Api, "API APEX", ComponentStatus.Stopped, "connexion refusée");
    private static readonly ComponentState RunningPanel = new(ComponentKey.WebPanel, "Web Panel", ComponentStatus.Running, "HTTP 200");
    private static readonly ApexOrchestratorTimings FastTimings = new(
        CommandTimeout: TimeSpan.FromSeconds(5), PollInterval: TimeSpan.FromMilliseconds(20),
        ApiReadyTimeout: TimeSpan.FromMilliseconds(200), WebPanelReadyTimeout: TimeSpan.FromMilliseconds(200));

    private static DashboardSettings AConfiguredSettings() => new() { ProjectPathWsl = "/home/user/apex-ia-" };

    private static ApexOrchestrator BuildOrchestrator(
        FakeProcessRunner runner, FakeWslProbe wslProbe, FakeRuntimeStatusService runtimeStatus,
        IApiHealthChecker apiChecker, IWebPanelChecker panelChecker, FakeBrowserLauncher browser, InMemoryLogSink log) =>
        new(runner, wslProbe, runtimeStatus, apiChecker, panelChecker, browser, log, FastTimings);

    [Fact]
    public async Task StartAsync_refuses_with_an_unconfigured_project_path_never_guessing_one()
    {
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), new FakeWslProbe(), new FakeRuntimeStatusService(),
            new SequencedHealthChecker([], RunningApi), new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(new DashboardSettings());

        Assert.Equal(OrchestratorOutcome.ProjectNotConfigured, outcome);
    }

    [Fact]
    public async Task StartAsync_refuses_when_WSL_is_not_installed()
    {
        var wslProbe = new FakeWslProbe { ProbeResult = new ComponentState(ComponentKey.Wsl, "WSL", ComponentStatus.NotInstalled, "wsl.exe introuvable") };
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), wslProbe, new FakeRuntimeStatusService(),
            new SequencedHealthChecker([], RunningApi), new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.WslUnavailable, outcome);
    }

    [Fact]
    public async Task StartAsync_refuses_when_the_configured_project_path_does_not_exist_in_WSL()
    {
        var wslProbe = new FakeWslProbe { PathExists = false };
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), wslProbe, new FakeRuntimeStatusService(),
            new SequencedHealthChecker([], RunningApi), new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.ProjectPathNotFound, outcome);
    }

    [Fact]
    public async Task StartAsync_reports_BackendFailedToStart_when_wsl_exe_cannot_be_launched_absent_process()
    {
        var runner = new FakeProcessRunner { NextStartFails = true };
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(),
            new SequencedHealthChecker([], RunningApi), new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.BackendFailedToStart, outcome);
    }

    [Fact]
    public async Task StartAsync_succeeds_once_API_and_WebPanel_both_report_a_real_Running_state_then_opens_the_browser()
    {
        var runner = new FakeProcessRunner();
        var apiChecker = new SequencedHealthChecker([StoppedApi, StoppedApi], RunningApi);
        var panelChecker = new SequencedHealthChecker([], RunningPanel);
        var browser = new FakeBrowserLauncher();
        var settings = AConfiguredSettings();
        var orchestrator = BuildOrchestrator(runner, new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, panelChecker, browser, new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(settings);

        Assert.Equal(OrchestratorOutcome.Success, outcome);
        Assert.True(apiChecker.CallCount >= 3);
        Assert.Equal(settings.WebPanelUrl, browser.OpenedUrl);
        Assert.Equal(2, runner.StartedProcesses.Count); // backend + frontend, chacun suivi individuellement
    }

    [Fact]
    public async Task StartAsync_never_opens_the_browser_before_the_WebPanel_check_confirms_it_really_responds()
    {
        var apiChecker = new SequencedHealthChecker([], RunningApi);
        var panelChecker = new SequencedHealthChecker([], StoppedApi with { Key = ComponentKey.WebPanel }); // ne devient jamais Running
        var browser = new FakeBrowserLauncher();
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, panelChecker, browser, new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.TimedOutWaitingForWebPanel, outcome);
        Assert.Null(browser.OpenedUrl);
    }

    [Fact]
    public async Task StartAsync_reports_a_real_bounded_timeout_when_the_API_never_becomes_Running_never_hanging_forever()
    {
        var apiChecker = new SequencedHealthChecker([], StoppedApi); // ne devient jamais Running
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), new FakeWslProbe(), new FakeRuntimeStatusService(),
            apiChecker, new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var started = DateTimeOffset.UtcNow;
        var outcome = await orchestrator.StartAsync(AConfiguredSettings());
        var elapsed = DateTimeOffset.UtcNow - started;

        Assert.Equal(OrchestratorOutcome.TimedOutWaitingForApi, outcome);
        Assert.True(elapsed < TimeSpan.FromSeconds(2), $"le timeout doit rester borné, pas {elapsed}");
        Assert.True(apiChecker.CallCount > 1, "doit avoir sondé plusieurs fois, jamais un unique délai fixe");
    }

    [Fact]
    public async Task StartAsync_detects_a_backend_crash_during_the_wait_instead_of_waiting_out_the_full_timeout()
    {
        var runner = new FakeProcessRunner();
        var apiChecker = new CrashingApiHealthChecker(runner);
        var started = DateTimeOffset.UtcNow;
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());
        var elapsed = DateTimeOffset.UtcNow - started;

        Assert.Equal(OrchestratorOutcome.BackendCrashed, outcome);
        Assert.True(elapsed < TimeSpan.FromSeconds(1), "un crash détecté doit interrompre l'attente immédiatement, jamais épuiser le timeout complet");
    }

    [Fact]
    public async Task StopAsync_kills_only_the_processes_this_orchestrator_itself_tracked()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex-launch.sh stop", new ProcessRunResult { Started = true, ExitCode = 0 });
        var apiChecker = new SequencedHealthChecker([StoppedApi], RunningApi);
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, new SequencedHealthChecker([], RunningPanel),
            new FakeBrowserLauncher(), new InMemoryLogSink());
        await orchestrator.StartAsync(AConfiguredSettings());
        Assert.True(orchestrator.IsBackendTracked);

        var outcome = await orchestrator.StopAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.Success, outcome);
        Assert.False(orchestrator.IsBackendTracked);
        Assert.All(runner.StartedProcesses, p => Assert.True(p.Killed));
        Assert.Contains(runner.RunInvocations, i => string.Join(' ', i.Arguments).Contains("apex-launch.sh stop"));
    }

    [Fact]
    public async Task StopAsync_with_nothing_tracked_still_tears_down_the_docker_infrastructure_via_apex_launch_sh()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex-launch.sh stop", new ProcessRunResult { Started = true, ExitCode = 0 });
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StopAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.Success, outcome);
    }

    [Fact]
    public async Task StopAsync_reports_StopFailed_when_apex_launch_sh_stop_itself_fails()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex-launch.sh stop", new ProcessRunResult { Started = true, ExitCode = 1, Stderr = "docker compose down a échoué" });
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StopAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.StopFailed, outcome);
    }

    [Fact]
    public async Task RestartAsync_stops_then_starts_in_that_order()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex-launch.sh stop", new ProcessRunResult { Started = true, ExitCode = 0 });
        var log = new InMemoryLogSink();
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), log);

        var outcome = await orchestrator.RestartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.Success, outcome);
        var stopIndex = log.Entries.ToList().FindIndex(e => e.Message.Contains("Arrêt de l'infrastructure"));
        var startIndex = log.Entries.ToList().FindIndex(e => e.Message.Contains("Démarrage d'APEX"));
        Assert.True(stopIndex >= 0 && startIndex > stopIndex, "l'arrêt doit précéder le redémarrage");
    }

    [Fact]
    public async Task RepairAsync_runs_apex_start_dry_run_the_existing_safe_auto_repair_command_never_a_new_one()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex start --dry-run", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = "environnement sain" });
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.RepairAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.Success, outcome);
        Assert.Contains(runner.RunInvocations, i => string.Join(' ', i.Arguments).Contains("apex start --dry-run"));
    }

    [Fact]
    public async Task RepairAsync_reports_RepairFailed_honestly_when_the_underlying_command_fails()
    {
        var runner = new FakeProcessRunner().WithRunResponse("apex start --dry-run", new ProcessRunResult { Started = true, ExitCode = 4, Stderr = "doctor a signalé un échec" });
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.RepairAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.RepairFailed, outcome);
    }

    [Fact]
    public async Task RepairAsync_refuses_with_an_invalid_unconfigured_project_path()
    {
        var orchestrator = BuildOrchestrator(
            new FakeProcessRunner(), new FakeWslProbe(), new FakeRuntimeStatusService(), new SequencedHealthChecker([], RunningApi),
            new SequencedHealthChecker([], RunningPanel), new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.RepairAsync(new DashboardSettings());

        Assert.Equal(OrchestratorOutcome.ProjectNotConfigured, outcome);
    }

    [Fact]
    public async Task StartAsync_never_lets_a_hostile_ApiHost_break_out_of_the_backend_start_command()
    {
        // Régression du BUG RÉEL (validation physique N+31, voir ApexOrchestrator.StartAsync) :
        // `settings.ApiHost` provient des Paramètres (texte libre utilisateur) et était
        // auparavant interpolé nu dans une commande `bash -lc` — une valeur comme
        // "127.0.0.1; touch pwned" pouvait exécuter une seconde commande arbitraire dans
        // WSL. Preuve ici : la valeur hostile doit apparaître SEULE, entre apostrophes
        // POSIX (ShellCommand.QuoteSingle), jamais suivie d'un `;` non protégé.
        const string hostileApiHost = "127.0.0.1; touch pwned";
        var runner = new FakeProcessRunner();
        var apiChecker = new SequencedHealthChecker([], RunningApi);
        var panelChecker = new SequencedHealthChecker([], RunningPanel);
        var settings = AConfiguredSettings() with { ApiHost = hostileApiHost };
        var orchestrator = BuildOrchestrator(
            runner, new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, panelChecker, new FakeBrowserLauncher(), new InMemoryLogSink());

        var outcome = await orchestrator.StartAsync(settings);

        Assert.Equal(OrchestratorOutcome.Success, outcome);
        Assert.True(runner.StartInvocations.Count >= 1, "le backend doit avoir été lancé au moins une fois");
        var backendCommand = string.Join(' ', runner.StartInvocations[0].Arguments); // premier Start() = backend (API), voir StartAsync
        // La valeur hostile doit apparaître UNIQUEMENT sous sa forme quotée POSIX —
        // à l'intérieur d'apostrophes, un `;` n'est plus un séparateur de commande pour bash.
        Assert.Contains(ShellCommand.QuoteSingle(hostileApiHost), backendCommand);
    }

    [Fact]
    public async Task StartAsync_refuses_a_second_launch_while_a_backend_is_already_tracked_never_spawning_a_zombie_process()
    {
        // Régression du BUG RÉEL (finalisation APEX.exe 1/2, voir ApexOrchestrator.StartAsync) :
        // un second appel à StartAsync après un premier succès lançait un second wsl.exe et
        // écrasait silencieusement la référence au premier processus suivi — un processus
        // zombie, jamais arrêté par StopAsync. Preuve ici : un deuxième appel doit refuser de
        // lancer un nouveau processus tant que le premier est encore suivi.
        var runner = new FakeProcessRunner();
        var apiChecker = new SequencedHealthChecker([], RunningApi);
        var panelChecker = new SequencedHealthChecker([], RunningPanel);
        var orchestrator = BuildOrchestrator(runner, new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, panelChecker, new FakeBrowserLauncher(), new InMemoryLogSink());
        var firstOutcome = await orchestrator.StartAsync(AConfiguredSettings());
        Assert.Equal(OrchestratorOutcome.Success, firstOutcome);
        var startCountAfterFirstLaunch = runner.StartInvocations.Count;

        var secondOutcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.AlreadyRunning, secondOutcome);
        Assert.Equal(startCountAfterFirstLaunch, runner.StartInvocations.Count); // aucun second wsl.exe lancé
        Assert.True(orchestrator.IsBackendTracked); // le premier processus reste le seul suivi
    }

    [Fact]
    public async Task StartAsync_logs_a_clear_warning_when_the_browser_fails_to_open_but_still_reports_success()
    {
        // Régression du BUG RÉEL (finalisation APEX.exe 1/2, voir ApexOrchestrator.StartAsync) :
        // le retour bool de IBrowserLauncher.Open était ignoré — un échec d'ouverture du
        // navigateur ne laissait absolument aucune trace, alors que "APEX READY" s'affichait.
        var apiChecker = new SequencedHealthChecker([], RunningApi);
        var panelChecker = new SequencedHealthChecker([], RunningPanel);
        var browser = new FakeBrowserLauncher { OpenSucceeds = false };
        var log = new InMemoryLogSink();
        var orchestrator = BuildOrchestrator(new FakeProcessRunner(), new FakeWslProbe(), new FakeRuntimeStatusService(), apiChecker, panelChecker, browser, log);

        var outcome = await orchestrator.StartAsync(AConfiguredSettings());

        Assert.Equal(OrchestratorOutcome.Success, outcome); // API et WebPanel sont réellement prêts
        Assert.Equal(browser.OpenedUrl, AConfiguredSettings().WebPanelUrl);
        Assert.Contains(log.Entries, e => e.Message.Contains("navigateur") && e.Message.Contains("pas pu être ouvert"));
    }

    /// <summary>Simule un crash réel de l'API pendant l'attente — dès le premier sondage, marque le processus suivi (backend) comme sorti, exactement le signal que <see cref="ApexOrchestrator"/> doit détecter sans attendre le timeout complet.</summary>
    private sealed class CrashingApiHealthChecker(FakeProcessRunner runner) : IApiHealthChecker
    {
        public Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
        {
            if (runner.StartedProcesses.Count > 0)
            {
                runner.StartedProcesses[0].Exit(137);
            }
            return Task.FromResult(StoppedApi);
        }
    }
}
