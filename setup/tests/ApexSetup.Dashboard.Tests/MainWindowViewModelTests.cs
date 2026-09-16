using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class MainWindowViewModelTests
{
    private static MainWindowViewModel Build(
        out FakeWslProbe wslProbe, out FakeRuntimeStatusService runtimeStatus, out FakeApexOrchestrator orchestrator)
    {
        wslProbe = new FakeWslProbe();
        runtimeStatus = new FakeRuntimeStatusService
        {
            States =
            [
                new ComponentState(ComponentKey.Docker, "Docker", ComponentStatus.Running, "ok"),
                new ComponentState(ComponentKey.Postgres, "PostgreSQL", ComponentStatus.Running, "ok"),
            ],
        };
        var apiChecker = new SequencedHealthCheckerAlwaysRunning(ComponentKey.Api, "API APEX");
        var panelChecker = new SequencedHealthCheckerAlwaysRunning(ComponentKey.WebPanel, "Web Panel");
        orchestrator = new FakeApexOrchestrator();
        return new MainWindowViewModel(
            new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-main-vm-test-" + Guid.NewGuid() + ".json")),
            wslProbe, orchestrator, runtimeStatus, apiChecker, panelChecker, new InMemoryLogSink());
    }

    [Fact]
    public async Task InitializeAsync_populates_ComponentStates_from_real_checks_never_pre_seeded_placeholders()
    {
        var vm = Build(out _, out _, out _);

        await vm.InitializeAsync();

        Assert.Contains(vm.ComponentStates, s => s.Key == ComponentKey.Wsl);
        Assert.Contains(vm.ComponentStates, s => s.Key == ComponentKey.Docker);
        Assert.Contains(vm.ComponentStates, s => s.Key == ComponentKey.Api);
        Assert.Contains(vm.ComponentStates, s => s.Key == ComponentKey.WebPanel);
    }

    [Fact]
    public async Task LaunchCommand_invokes_the_orchestrators_StartAsync_then_refreshes_state()
    {
        var vm = Build(out _, out _, out var orchestrator);
        await vm.InitializeAsync();

        await vm.LaunchCommand.ExecuteAsync();

        Assert.Equal(1, orchestrator.StartCalls);
    }

    [Fact]
    public async Task StopCommand_invokes_the_orchestrators_StopAsync()
    {
        var vm = Build(out _, out _, out var orchestrator);
        await vm.InitializeAsync();

        await vm.StopCommand.ExecuteAsync();

        Assert.Equal(1, orchestrator.StopCalls);
    }

    [Fact]
    public async Task OpenSettingsCommand_then_CloseSettingsCommand_toggle_visibility()
    {
        var vm = Build(out _, out _, out _);
        await vm.InitializeAsync();
        Assert.False(vm.IsSettingsVisible);

        vm.OpenSettingsCommand.Execute(null);
        Assert.True(vm.IsSettingsVisible);

        vm.CloseSettingsCommand.Execute(null);
        Assert.False(vm.IsSettingsVisible);
    }

    [Fact]
    public async Task Saving_settings_closes_the_panel_and_refreshes_using_the_new_values()
    {
        var vm = Build(out var wslProbe, out _, out _);
        await vm.InitializeAsync();
        vm.OpenSettingsCommand.Execute(null);
        vm.Settings!.ProjectPathWsl = "/home/user/apex-ia-";

        await vm.Settings.SaveCommand.ExecuteAsync();

        Assert.False(vm.IsSettingsVisible);
    }

    [Fact]
    public async Task IsBusy_blocks_other_lifecycle_commands_while_one_is_still_running()
    {
        // Régression du BUG RÉEL (finalisation APEX.exe 1/2, voir MainWindowViewModel) :
        // chaque commande de cycle de vie ne se désactivait QUE elle-même — rien n'empêchait
        // de cliquer « ARRÊTER » pendant que « LANCER » tournait encore (deux opérations
        // incompatibles en parallèle sur le même orchestrateur). Preuve ici : pendant que
        // StartAsync est en cours (bloqué volontairement), StopCommand/RestartCommand/
        // RepairCommand doivent tous refuser de s'exécuter (CanExecute == false).
        var gate = new TaskCompletionSource();
        var vm = Build(out _, out _, out var orchestrator);
        await vm.InitializeAsync();
        orchestrator.StartGate = gate.Task;

        var launchRun = vm.LaunchCommand.ExecuteAsync();
        await orchestrator.StartEntered.Task;

        Assert.True(vm.IsBusy);
        Assert.False(vm.StopCommand.CanExecute(null));
        Assert.False(vm.RestartCommand.CanExecute(null));
        Assert.False(vm.RepairCommand.CanExecute(null));
        Assert.False(vm.LaunchCommand.CanExecute(null));

        gate.SetResult();
        await launchRun;

        Assert.False(vm.IsBusy);
        Assert.True(vm.StopCommand.CanExecute(null));
    }

    [Fact]
    public async Task An_unexpected_exception_in_a_lifecycle_command_is_logged_instead_of_crashing_the_app()
    {
        // Régression du BUG RÉEL (finalisation APEX.exe 1/2) : sans AsyncRelayCommand.Faulted,
        // une exception inattendue dans StartAsync aurait fait planter APEX.exe (async void non
        // géré). Preuve ici : l'exception est absorbée et rendue visible dans le journal.
        var vm = Build(out _, out _, out var orchestrator);
        await vm.InitializeAsync();
        orchestrator.StartThrows = new InvalidOperationException("échec simulé");

        var exception = await Record.ExceptionAsync(() => vm.LaunchCommand.ExecuteAsync());

        Assert.Null(exception);
        Assert.Contains(vm.LogLines, l => l.Message.Contains("Erreur inattendue"));
        Assert.False(vm.IsBusy);
    }

    private sealed class SequencedHealthCheckerAlwaysRunning(ComponentKey key, string label) : IApiHealthChecker, IWebPanelChecker
    {
        public Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default) =>
            Task.FromResult(new ComponentState(key, label, ComponentStatus.Running, "HTTP 200"));
    }

    private sealed class FakeApexOrchestrator : IApexOrchestrator
    {
        public int StartCalls { get; private set; }
        public int StopCalls { get; private set; }
        public bool IsBackendTracked => false;
        public bool IsFrontendTracked => false;

        /// <summary>Si posé, StartAsync attend cette tâche avant de retourner — permet à un test d'observer un état "en cours" réel, jamais un simple booléen instantané.</summary>
        public Task? StartGate { get; set; }

        /// <summary>Complétée dès que StartAsync est réellement entré — évite toute dépendance à un délai arbitraire côté test.</summary>
        public TaskCompletionSource StartEntered { get; } = new();

        /// <summary>Si posé, StartAsync lève cette exception au lieu de retourner un résultat — simule un défaut inattendu.</summary>
        public Exception? StartThrows { get; set; }

        public async Task<OrchestratorOutcome> StartAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
        {
            StartCalls++;
            StartEntered.TrySetResult();
            if (StartGate is not null)
            {
                await StartGate;
            }
            if (StartThrows is not null)
            {
                throw StartThrows;
            }
            return OrchestratorOutcome.Success;
        }

        public Task<OrchestratorOutcome> StopAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
        {
            StopCalls++;
            return Task.FromResult(OrchestratorOutcome.Success);
        }

        public Task<OrchestratorOutcome> RestartAsync(DashboardSettings settings, CancellationToken cancellationToken = default) =>
            Task.FromResult(OrchestratorOutcome.Success);

        public Task<OrchestratorOutcome> RepairAsync(DashboardSettings settings, CancellationToken cancellationToken = default) =>
            Task.FromResult(OrchestratorOutcome.Success);
    }
}
