using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using ApexSetup.Dashboard.Views;
using Avalonia;
using Avalonia.Controls;
using Avalonia.VisualTree;
using Avalonia.Headless;
using Avalonia.Threading;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Application de test : la vraie <see cref="App"/> (thème, styles), sur la plateforme sans écran d'Avalonia.</summary>
public sealed class HeadlessPanel
{
    public static AppBuilder BuildAvaloniaApp() =>
        AppBuilder.Configure<App>().UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = true });

    public static readonly Lazy<HeadlessUnitTestSession> Session = new(() => HeadlessUnitTestSession.StartNew(typeof(HeadlessPanel)));
}

/// <summary>
/// Constaté sur Windows 11 réel (10/10/2026, 0.4.3) : environ 2 s après « Démarrer APEX », boîte « APEX n'a pas pu
/// s'ouvrir — InvalidOperationException : Call from invalid thread ». L'interface était touchée hors de son fil.
/// Ces tests pilotent la VRAIE fenêtre (MainWindow + vue-modèle + vrais boutons) avec la même règle de fil que Windows.
/// </summary>
public class WindowThreadTests
{
    private sealed class Starter : IDetachedProcessStarter
    {
        public TaskCompletionSource<int> Exit { get; } = new();
        public bool Fails { get; init; }

        public DetachedProcess Start(string executable, IReadOnlyList<string> arguments) =>
            Fails ? DetachedProcess.NotStarted("wsl.exe introuvable") : new(true, null, Exit.Task);
    }

    private sealed class NoBrowser : IBrowserLauncher
    {
        public string? Open(string url) => null;
    }

    private sealed record WindowRig(MainWindowViewModel Vm, MainWindow Window, Starter Starter, FakeBackendHost Host);

    /// <summary>La vraie fenêtre, câblée comme App le fait (actions destinées à l'interface postées sur son fil).</summary>
    private static async Task<WindowRig> OpenWindow(bool startFails = false)
    {
        var host = new FakeBackendHost();
        var redactor = new Redactor();
        var log = new InMemoryLogSink(redactor);
        var client = new StatusClient(new HttpClient());
        var service = new PanelStatusService(host, client);
        var store = new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-window-" + Guid.NewGuid() + ".json"));
        await store.SaveAsync(new DashboardSettings { WslDistro = "Ubuntu-24.04", ApiPort = 1 });
        var starter = new Starter { Fails = startFails };
        var control = new PanelControl(new BackendLauncher(starter, new FakeProcessRunner(), wsl: true), client, new NoBrowser());
        var vm = new MainWindowViewModel(store, service, log, redactor,
            problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem),
            action => Dispatcher.UIThread.Post(action), control);
        var window = new MainWindow { DataContext = vm };
        window.Show();
        await Task.Run(vm.InitializeAsync);
        await Settle();
        return new WindowRig(vm, window, starter, host);
    }

    private static async Task Until(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(20);
        while (!condition() && DateTime.UtcNow < deadline)
        {
            await Task.Delay(50);
        }
        await Settle();
    }

    private static async Task CloseWindow(WindowRig rig)
    {
        await Until(() => !rig.Vm.IsChecking && !rig.Vm.StartCommand.IsRunning);
        rig.Window.Close();
        await Settle();
    }

    [Fact]
    public async Task Clicking_Start_in_the_real_window_never_touches_the_interface_from_another_thread()
    {
        var outcome = await HeadlessPanel.Session.Value.Dispatch(async () =>
        {
            var rig = await OpenWindow();
            Assert.True(rig.Vm.CanStart, rig.Vm.Headline);
            var start = rig.Window.FindControl<Button>("TopStartButton")!;
            Assert.True(start.IsEffectivelyEnabled);

            string? error = null;
            try
            {
                await rig.Vm.StartCommand.ExecuteAsync(); // ce que fait le clic (sans « async void » : l'erreur remonte ici)
                await Until(() => rig.Vm.Snapshot?.Cause == NotReadyCause.BackendStarting);
                // APEX s'arrête aussitôt (démarrage échoué) : « Démarrer APEX » redevient possible.
                rig.Starter.Exit.SetResult(1);
                await Until(() => rig.Vm.CanStart);
            }
            catch (InvalidOperationException ex)
            {
                error = ex.ToString();
            }
            var cause = rig.Vm.Snapshot?.Cause;
            var canStartAgain = start.IsEffectivelyEnabled;
            await CloseWindow(rig);
            return (error, cause, canStartAgain);
        }, CancellationToken.None);

        Assert.Null(outcome.error);
        Assert.Equal(NotReadyCause.StartFailed, outcome.cause);
        Assert.True(outcome.canStartAgain); // le bouton a bien été réactivé, sur le fil d'interface
    }

    [Fact]
    public async Task A_start_that_fails_at_once_re_enables_Start_on_the_interface_thread()
    {
        // L'état du bouton change exactement à la fin de l'action : le cas qui fermait APEX.exe.
        var outcome = await HeadlessPanel.Session.Value.Dispatch(async () =>
        {
            var rig = await OpenWindow(startFails: true);
            var start = rig.Window.FindControl<Button>("TopStartButton")!;
            string? error = null;
            try
            {
                await rig.Vm.StartCommand.ExecuteAsync();
                await Until(() => rig.Vm.CanStart);
            }
            catch (InvalidOperationException ex)
            {
                error = ex.ToString();
            }
            var enabled = start.IsEffectivelyEnabled;
            var message = rig.Vm.ActionMessage;
            await CloseWindow(rig);
            return (error, enabled, message);
        }, CancellationToken.None);

        Assert.Null(outcome.error);
        Assert.True(outcome.enabled);
        Assert.Contains("Démarrage impossible", outcome.message);
    }

    [Fact]
    public async Task Saving_the_settings_in_the_real_window_never_touches_the_interface_from_another_thread()
    {
        // Même défaut, autre bouton : la fenêtre des paramètres était créée hors du fil d'interface.
        var outcome = await HeadlessPanel.Session.Value.Dispatch(async () =>
        {
            var rig = await OpenWindow();
            await Until(() => rig.Vm.Settings is not null);
            rig.Vm.OpenSettingsCommand.Execute(null);
            await Settle();
            string? error = null;
            try
            {
                await rig.Vm.Settings!.SaveCommand.ExecuteAsync();
                await Settle();
            }
            catch (InvalidOperationException ex)
            {
                error = ex.ToString();
            }
            await CloseWindow(rig);
            return error;
        }, CancellationToken.None);

        Assert.Null(outcome);
    }

    /// <summary>Ce que Windows réel a fait le 10/10/2026 (0.4.4) : la fenêtre enfant de l'hôte natif est refusée au rattachement.</summary>
    private sealed class RefusedNativeHost : Control
    {
        protected override void OnAttachedToVisualTree(VisualTreeAttachmentEventArgs e)
        {
            base.OnAttachedToVisualTree(e);
            throw new InvalidOperationException(
                "Unable to create child window for native control host. Application manifest with supported OS list might be required.");
        }
    }

    [Fact]
    public async Task When_Windows_refuses_the_display_window_APEX_stays_open_and_uses_the_browser()
    {
        var guardFile = Path.Combine(Path.GetTempPath(), "apex-guard-" + Guid.NewGuid().ToString("N"), "webview-attempt.json");
        var outcome = await HeadlessPanel.Session.Value.Dispatch(async () =>
        {
            await using var server = EmbeddedAssistantTests.ApexServer();
            var port = new Uri(server.BaseUrl).Port;
            var host = new FakeBackendHost();
            EmbeddedAssistantTests.Serving(host, port);
            var redactor = new Redactor();
            var log = new InMemoryLogSink(redactor);
            var client = new StatusClient(new HttpClient());
            var service = new PanelStatusService(host, client);
            var store = new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-window-" + Guid.NewGuid() + ".json"));
            await store.SaveAsync(new DashboardSettings { ApiPort = port });
            var control = new PanelControl(new BackendLauncher(new Starter(), new FakeProcessRunner(), wsl: true), client, new NoBrowser());
            var vm = new MainWindowViewModel(store, service, log, redactor,
                problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem),
                action => Dispatcher.UIThread.Post(action), control, embeddedAssistant: true);
            var guard = new EmbeddedDisplayGuard(guardFile);
            var window = new MainWindow { DataContext = vm, DisplayGuard = guard, AssistantHostFactory = _ => new RefusedNativeHost() };
            window.Show();
            string? error = null;
            try
            {
                await Task.Run(vm.InitializeAsync); // APEX répond : l'assistant s'ouvre dans la fenêtre… qui est refusée
                await Until(() => !vm.IsEmbeddedAssistant);
            }
            catch (InvalidOperationException ex)
            {
                error = ex.ToString();
            }
            var result = (error, vm.IsEmbeddedAssistant, vm.CanRetryEmbedded, vm.AssistantMessage, window.IsVisible,
                guard.Interrupted(), File.Exists(guardFile),
                log.Entries.Any(e => e.Message.Contains("Unable to create child window")));
            window.Close();
            await Settle();
            return result;
        }, CancellationToken.None);

        Assert.Null(outcome.Item1);                    // aucune exception : APEX.exe reste ouvert
        Assert.False(outcome.Item2);                   // l'assistant passe au navigateur
        Assert.True(outcome.Item3);                    // « Réessayer dans la fenêtre » proposé
        Assert.Contains("n'a pas pu être créée", outcome.Item4);
        Assert.Contains("navigateur", outcome.Item4);
        Assert.True(outcome.Item5);                    // la fenêtre est toujours là
        Assert.Null(outcome.Item6);                    // échec propre : pas pris pour un plantage au lancement suivant
        Assert.False(outcome.Item7);
        Assert.True(outcome.Item8);                    // la raison exacte est dans le journal
    }

    [Fact]
    public void The_APEX_exe_manifest_declares_Windows_10_and_11()
    {
        // Sans cette déclaration, Windows réel refuse la fenêtre enfant de l'hôte natif (constaté le 10/10/2026).
        var project = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "src", "ApexSetup.Dashboard"));
        var manifest = System.Xml.Linq.XDocument.Load(Path.Combine(project, "app.manifest"));
        System.Xml.Linq.XNamespace compat = "urn:schemas-microsoft-com:compatibility.v1";
        var supported = manifest.Descendants(compat + "supportedOS").Select(e => (string?)e.Attribute("Id")).ToList();
        Assert.Contains("{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}", supported);
        Assert.Contains("<ApplicationManifest>app.manifest</ApplicationManifest>",
            File.ReadAllText(Path.Combine(project, "ApexSetup.Dashboard.csproj")));
    }

    [Fact]
    public async Task A_button_whose_command_finishes_on_a_background_thread_is_updated_on_the_interface_thread()
    {
        // Le mécanisme exact : une action de bouton se termine sur un fil d'arrière-plan (après un await) et l'état
        // du bouton change à ce moment-là — sous Windows, « Call from invalid thread », et APEX.exe se ferme.
        var outcome = await HeadlessPanel.Session.Value.Dispatch(async () =>
        {
            var command = new Mvvm.AsyncRelayCommand(async _ => await Task.Delay(50).ConfigureAwait(false));
            var button = new Button { Content = "Démarrer APEX", Command = command };
            var window = new Window { Content = button };
            window.Show();
            await Settle();
            string? error = null;
            try
            {
                await command.ExecuteAsync();
            }
            catch (InvalidOperationException ex)
            {
                error = ex.Message;
            }
            await Settle();
            var enabled = button.IsEffectivelyEnabled;
            window.Close();
            Dispatcher.UIThread.RunJobs();
            return (error, enabled);
        }, CancellationToken.None);

        Assert.Null(outcome.error);
        Assert.True(outcome.enabled); // le bouton est bien redevenu cliquable
    }

    /// <summary>Laisse le fil d'interface traiter tout ce qui a été posté.</summary>
    private static async Task Settle()
    {
        for (var i = 0; i < 10; i++)
        {
            await Task.Delay(30);
            Dispatcher.UIThread.RunJobs();
        }
    }
}
