using ApexSetup.Core.RuntimeAdapter;
using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class CheckViewModelTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-gui-check-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-gui-check-dest-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        Directory.Delete(_destination, recursive: true);
    }

    [Fact]
    public async Task Never_triggers_an_installation_and_allows_Continue_when_healthy()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession { ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest };
        var vm = new CheckViewModel(service, session, new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.True(vm.Result!.Success);
        Assert.NotEmpty(vm.Checks);
        Assert.True(vm.ContinueCommand.CanExecute(null));
        Assert.False(Directory.Exists(Path.Combine(_destination, "artifacts")));
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
    }

    [Fact]
    public async Task Blocks_Continue_when_the_gate_refuses_never_lets_the_user_proceed_past_a_real_gate_block()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorPass)
            .WithResponse("gate", SamplePayloads.GateFailBlocking);
        var service = TestServiceFactory.Build(runner);
        var session = new SetupSession { ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest };
        var vm = new CheckViewModel(service, session, new FakeNavigationService());

        await vm.OnNavigatedToAsync();

        Assert.False(vm.Result!.Success);
        Assert.True(vm.Result.HasBlockingFailure);
        Assert.False(vm.ContinueCommand.CanExecute(null));
    }

    // -----------------------------------------------------------------
    // PHASE 10 — revue adversariale : course entre le déclenchement
    // automatique (OnNavigatedToAsync, appelé par MainWindowViewModel à
    // chaque arrivée sur l'écran) et le bouton "Revérifier" manuel.
    // -----------------------------------------------------------------
    // AVANT correctif, OnNavigatedToAsync appelait RunCheckAsync()
    // directement, hors du garde RecheckCommand.IsRunning — un clic sur
    // "Revérifier" pendant le check automatique déclenchait une DEUXIÈME
    // exécution concurrente. Reproduit ici en comptant les VRAIS appels au
    // Runtime (version/doctor/gate) : avant le correctif, un déclenchement
    // automatique suivi immédiatement d'un déclenchement manuel produisait
    // 2 appels à "version" (6 appels au total) ; après le correctif
    // (OnNavigatedToAsync route par RecheckCommand.ExecuteAsync, même garde
    // IsRunning que le bouton), le second déclenchement pendant que le
    // premier tourne encore est un no-op réel : exactement 1 appel à
    // "version" (3 appels au total).
    [Fact]
    public async Task Concurrent_automatic_and_manual_recheck_never_run_two_checks_at_once()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new GatedCountingRuntimeProcessRunner();
        var service = TestServiceFactory.Build(runner);
        var session = new SetupSession { ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest };
        var vm = new CheckViewModel(service, session, new FakeNavigationService());

        var autoTrigger = vm.OnNavigatedToAsync(); // simule l'arrivée sur l'écran (MainWindowViewModel.NavigateTo)
        await runner.FirstCallStarted.Task; // laisse le premier check RÉELLEMENT démarrer avant de tenter le second
        var manualTrigger = vm.RecheckCommand.ExecuteAsync(); // clic manuel pendant que l'automatique tourne encore

        runner.Release.SetResult(); // laisse les deux tentatives se terminer
        await Task.WhenAll(autoTrigger, manualTrigger);

        // "version"/"doctor" : un seul appel chacun -> preuve directe qu'UNE
        // SEULE exécution de RunCheckAsync a eu lieu (sinon 2 de chaque).
        // "gate" : DEUX appels, mais c'est le comportement LÉGITIME d'une
        // SEULE exécution de CheckAsync (SetupApplicationService.BuildPlanAsync
        // interroge le Gate une première fois pour classifier les dépendances
        // du plan, PUIS PreflightChecker.RunAsync l'interroge à nouveau pour
        // CheckGateExploitable — jamais lié à une double exécution).
        Assert.Equal(1, runner.CallCount("version"));
        Assert.Equal(1, runner.CallCount("doctor"));
        Assert.Equal(2, runner.CallCount("gate"));
        Assert.NotNull(vm.Result);
    }

    /// <summary>
    /// Bloque chaque appel jusqu'à <see cref="Release"/> — permet de
    /// garantir de façon DÉTERMINISTE (jamais un pari sur un délai) qu'une
    /// deuxième invocation est tentée PENDANT que la première est
    /// RÉELLEMENT en cours, plutôt que déjà terminée.
    /// </summary>
    private sealed class GatedCountingRuntimeProcessRunner : IRuntimeProcessRunner
    {
        private readonly Dictionary<string, int> _counts = new();
        private readonly object _lock = new();
        public readonly TaskCompletionSource FirstCallStarted = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public readonly TaskCompletionSource Release = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public int CallCount(string command)
        {
            lock (_lock)
            {
                return _counts.GetValueOrDefault(command);
            }
        }

        public async Task<ProcessInvocationResult> RunAsync(
            string executable, IReadOnlyList<string> arguments, TimeSpan timeout, CancellationToken cancellationToken = default)
        {
            var command = arguments.Count > 0 ? arguments[0] : string.Empty;
            lock (_lock)
            {
                _counts[command] = _counts.GetValueOrDefault(command) + 1;
            }
            FirstCallStarted.TrySetResult();
            await Release.Task.ConfigureAwait(false);

            var stdout = command switch
            {
                "version" => SamplePayloads.Version,
                "doctor" => SamplePayloads.DoctorPass,
                "gate" => SamplePayloads.GatePassAuthorized,
                _ => "{}",
            };
            return new ProcessInvocationResult { ProcessStarted = true, TimedOut = false, ExitCode = 0, Stdout = stdout, Stderr = string.Empty };
        }
    }
}
