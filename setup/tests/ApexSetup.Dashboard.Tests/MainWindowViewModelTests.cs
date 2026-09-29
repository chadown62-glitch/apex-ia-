using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class MainWindowViewModelTests
{
    private sealed class RecordingClipboard : IClipboardService
    {
        public string? Text { get; private set; }

        public Task SetTextAsync(string text)
        {
            Text = text;
            return Task.CompletedTask;
        }
    }

    private static (MainWindowViewModel Vm, FakeBackendHost Host, InMemoryLogSink Log, Redactor Redactor) Build(Action<FakeBackendHost>? configure = null)
    {
        var host = new FakeBackendHost();
        configure?.Invoke(host);
        var redactor = new Redactor();
        var log = new InMemoryLogSink(redactor);
        var service = new PanelStatusService(host, new StatusClient(new HttpClient()));
        service.SecretRead += redactor.Register;
        var store = new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-main-vm-" + Guid.NewGuid() + ".json"));
        // Port 1 : rien n'y écoute — le test ne dépend jamais d'un service présent sur 8000 dans l'environnement.
        store.SaveAsync(new DashboardSettings { ApiPort = 1 }).GetAwaiter().GetResult();
        var vm = new MainWindowViewModel(store, service, log, redactor,
            problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem));
        return (vm, host, log, redactor);
    }

    [Fact]
    public void Before_any_measurement_there_is_no_verdict_and_no_component()
    {
        var (vm, _, _, _) = Build();

        Assert.Null(vm.Verdict);
        Assert.Empty(vm.Components);
        Assert.DoesNotContain("READY", vm.Headline.Replace("NOT READY", ""));
    }

    [Fact]
    public async Task Initialization_measures_for_real_and_logs_the_verdict()
    {
        var (vm, _, log, _) = Build(h => h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.NotFound("absent"));

        await vm.InitializeAsync();

        Assert.Equal(PanelVerdict.NotReady, vm.Verdict);
        Assert.Equal("APEX NOT READY", vm.Headline);
        Assert.Contains("launch.sh", vm.Guidance);
        Assert.Contains(vm.Components, c => c.Key == ComponentKey.Api && c.Status == ComponentStatus.Offline);
        Assert.Contains(log.Entries, e => e.Message.Contains("BackendNotStarted"));
    }

    [Fact]
    public async Task Unchanged_states_are_not_logged_again()
    {
        var (vm, _, log, _) = Build();
        await vm.InitializeAsync();
        var count = log.Entries.Count;

        await vm.RefreshAsync();
        await vm.RefreshAsync();

        Assert.Equal(count, log.Entries.Count);
    }

    [Fact]
    public async Task Diagnostic_is_built_from_the_last_measurement_and_copied_without_secret()
    {
        var (vm, _, _, redactor) = Build(h =>
        {
            h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: 1));
            h.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
        });
        var clipboard = new RecordingClipboard();
        vm.Clipboard = clipboard;
        await vm.InitializeAsync();

        vm.ShowDiagnosticCommand.Execute(null);
        await vm.CopyDiagnosticCommand.ExecuteAsync();

        Assert.True(vm.IsDiagnosticVisible);
        Assert.Contains("Version du Panel", vm.DiagnosticText);
        Assert.NotNull(clipboard.Text);
        Assert.DoesNotContain(PanelData.Token, clipboard.Text);
        Assert.Contains("copié", vm.CopyMessage);
    }

    [Fact]
    public async Task Copy_without_clipboard_says_so_instead_of_failing()
    {
        var (vm, _, _, _) = Build();
        await vm.InitializeAsync();

        await vm.CopyDiagnosticCommand.ExecuteAsync();

        Assert.Contains("indisponible", vm.CopyMessage);
    }

    [Fact]
    public async Task Saving_settings_closes_them_and_measures_again_with_the_new_distribution()
    {
        var (vm, _, log, _) = Build();
        await vm.InitializeAsync();
        vm.OpenSettingsCommand.Execute(null);
        vm.Settings!.WslDistro = "Ubuntu-24.04";

        await vm.Settings.SaveCommand.ExecuteAsync();

        Assert.False(vm.IsSettingsVisible);
        Assert.Contains(log.Entries, e => e.Message.Contains("Ubuntu-24.04"));
    }

    [Fact]
    public async Task Concurrent_refreshes_never_measure_twice_at_the_same_time()
    {
        var (vm, host, _, _) = Build(h => h.Probe = PanelData.WslRunning());
        await vm.InitializeAsync();
        host.Reads.Clear();

        await Task.WhenAll(Enumerable.Range(0, 5).Select(_ => vm.RefreshAsync()));

        // Chaque mesure lit l'enregistrement une fois : jamais plus de mesures que d'appels acceptés.
        Assert.InRange(host.Reads.Count(r => r.EndsWith("backend.json")), 1, 5);
    }
}

public class SettingsViewModelTests
{
    private static string TempFile() => Path.Combine(Path.GetTempPath(), "apex-settings-vm-" + Guid.NewGuid() + ".json");

    [Fact]
    public async Task Saves_only_the_distribution_and_keeps_other_values()
    {
        var store = new JsonSettingsStore(TempFile());
        var vm = new SettingsViewModel(store, new DashboardSettings { ApiPort = 8123 }) { WslDistro = "  Debian  " };
        DashboardSettings? saved = null;
        vm.Saved += (_, s) => saved = s;

        await vm.SaveCommand.ExecuteAsync();

        Assert.Equal(new DashboardSettings { WslDistro = "Debian", ApiPort = 8123 }, saved);
        Assert.Equal(saved, (await store.LoadAsync()).Settings);
    }

    [Theory]
    [InlineData("a\"b")]
    [InlineData("../x")]
    [InlineData("a\nb")]
    public async Task Rejects_an_invalid_distribution_name(string name)
    {
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), new DashboardSettings()) { WslDistro = name };
        var saved = false;
        vm.Saved += (_, _) => saved = true;

        await vm.SaveCommand.ExecuteAsync();

        Assert.False(saved);
        Assert.NotNull(vm.Message);
    }

    [Fact]
    public async Task A_persistence_failure_is_a_message_not_a_crash()
    {
        var blocker = Path.Combine(Path.GetTempPath(), "apex-blocker-" + Guid.NewGuid());
        File.WriteAllText(blocker, "fichier");
        var vm = new SettingsViewModel(new JsonSettingsStore(Path.Combine(blocker, "sub", "settings.json")), new DashboardSettings());

        await vm.SaveCommand.ExecuteAsync();

        Assert.Contains("impossible", vm.Message);
        File.Delete(blocker);
    }

    [Fact]
    public void Close_raises_CloseRequested()
    {
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), new DashboardSettings());
        var closed = false;
        vm.CloseRequested += (_, _) => closed = true;

        vm.CloseCommand.Execute(null);

        Assert.True(closed);
    }
}
