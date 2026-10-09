using System.Text;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// 0.4.1, après le premier essai sur Windows 11 réel (09/10/2026) :
/// <list type="bullet">
/// <item>« Ouvrir APEX » restait grisé : une mesure de 6 s, enchaînée sans pause, vidait l'accès à APEX au
/// début de chaque mesure — l'accès n'est désormais remplacé qu'à la fin ;</item>
/// <item>l'utilisateur veut l'interface DANS APEX.exe, pas dans le navigateur : l'assistant s'affiche dans la
/// fenêtre, suit les arrêts et redémarrages d'APEX, et repasse au navigateur (avec la raison) si le composant
/// d'affichage manque.</item>
/// </list>
/// </summary>
public class EmbeddedAssistantTests
{
    private sealed class RecordingBrowser : IBrowserLauncher
    {
        public List<string> Opened { get; } = [];

        public string? Open(string url)
        {
            Opened.Add(url);
            return null;
        }
    }

    private sealed class NoStarter : IDetachedProcessStarter
    {
        public DetachedProcess Start(string executable, IReadOnlyList<string> arguments) =>
            new(true, null, new TaskCompletionSource<int>().Task);
    }

    private sealed record Rig(MainWindowViewModel Vm, PanelStatusService Service, FakeBackendHost Host, RecordingBrowser Browser,
        InMemoryLogSink Log);

    private static int _codes;

    private static LocalHttpTestServer ApexServer() => new(async ctx =>
    {
        var path = ctx.Request.Url!.AbsolutePath;
        var authorized = ctx.Request.Headers["Authorization"] == $"Bearer {PanelData.Token}";
        string body;
        int status;
        if (!authorized)
        {
            (status, body) = (401, "{\"detail\":\"jeton invalide\"}");
        }
        else if (path == "/api/v1/status")
        {
            (status, body) = (200, PanelData.StatusJson());
        }
        else if (path == "/api/v1/ui/launch-code" && ctx.Request.HttpMethod == "POST")
        {
            var n = Interlocked.Increment(ref _codes);
            (status, body) = (200, $"{{\"status\":\"real\",\"data\":{{\"login_path\":\"/ui/login?code=c0de_{n:D32}\",\"expires_in_s\":60,\"ui_available\":true,\"ui_detail\":\"ok\"}},\"detail\":null}}");
        }
        else
        {
            (status, body) = (404, "{\"detail\":\"Not Found\"}");
        }
        ctx.Response.StatusCode = status;
        var bytes = Encoding.UTF8.GetBytes(body);
        ctx.Response.ContentType = "application/json";
        await ctx.Response.OutputStream.WriteAsync(bytes);
        ctx.Response.Close();
    });

    private static void Serving(FakeBackendHost host, int port, int pid = 4242)
    {
        host.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: port, pid: pid));
        host.Files[$"/proc/{pid}/cmdline"] = FileRead.Ok("python\0-m\0apex.cli.main\0serve");
        host.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
    }

    private static void Stopped(FakeBackendHost host) => host.Files.Remove("~/" + BackendDiscovery.RelativePath);

    private static Rig Build(int port, bool embedded = true)
    {
        var host = new FakeBackendHost();
        Serving(host, port);
        var redactor = new Redactor();
        var log = new InMemoryLogSink(redactor);
        var client = new StatusClient(new HttpClient());
        var service = new PanelStatusService(host, client);
        var store = new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-embedded-" + Guid.NewGuid() + ".json"));
        store.SaveAsync(new DashboardSettings { ApiPort = port }).GetAwaiter().GetResult();
        var browser = new RecordingBrowser();
        var control = new PanelControl(new BackendLauncher(new NoStarter(), new FakeProcessRunner(), wsl: true), client, browser);
        var vm = new MainWindowViewModel(store, service, log, redactor,
            problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem), null, control,
            embeddedAssistant: embedded);
        return new Rig(vm, service, host, browser, log);
    }

    [Fact]
    public async Task Access_to_APEX_is_kept_while_a_slow_measurement_runs_so_open_never_greys_out()
    {
        await using var server = ApexServer();
        var rig = Build(new Uri(server.BaseUrl).Port, embedded: false);
        await rig.Vm.InitializeAsync();
        Assert.NotNull(rig.Service.LastAccess);
        Assert.True(rig.Vm.CanOpen);

        var gate = new TaskCompletionSource();
        rig.Host.BeforeProbe = () => gate.Task;
        var slow = rig.Vm.RefreshAsync();
        Assert.False(slow.IsCompleted);
        // Pendant la mesure (6 s sur Windows réel), l'accès de la mesure précédente reste valable : bouton actif, clic utile.
        Assert.NotNull(rig.Service.LastAccess);
        Assert.True(rig.Vm.CanOpen);
        await rig.Vm.OpenUiCommand.ExecuteAsync();
        Assert.Single(rig.Browser.Opened);

        gate.SetResult();
        await slow;
        Assert.NotNull(rig.Service.LastAccess);

        // APEX arrêté : l'accès disparaît à la fin de la mesure qui le constate, pas avant.
        rig.Host.BeforeProbe = null;
        Stopped(rig.Host);
        await rig.Vm.RefreshAsync();
        Assert.Null(rig.Service.LastAccess);
        Assert.False(rig.Vm.CanOpen);
    }

    [Fact]
    public async Task The_assistant_opens_inside_the_window_never_in_the_browser()
    {
        await using var server = ApexServer();
        var port = new Uri(server.BaseUrl).Port;
        var rig = Build(port);

        await rig.Vm.InitializeAsync();

        Assert.True(rig.Vm.IsEmbeddedAssistant);
        Assert.True(rig.Vm.IsAssistantPage);
        Assert.NotNull(rig.Vm.AssistantUrl);
        Assert.StartsWith($"http://127.0.0.1:{port}/ui/login?code=c0de_", rig.Vm.AssistantUrl);
        Assert.DoesNotContain(PanelData.Token, rig.Vm.AssistantUrl);
        Assert.True(rig.Vm.IsAssistantHostVisible);
        Assert.Empty(rig.Browser.Opened);
        Assert.DoesNotContain(rig.Log.Entries, e => e.Message.Contains("code="));
        Assert.Contains(rig.Log.Entries, e => e.Message.Contains("ouvert dans cette fenêtre"));

        // Le navigateur reste disponible, sur demande explicite.
        await rig.Vm.OpenInBrowserCommand.ExecuteAsync();
        Assert.Single(rig.Browser.Opened);
    }

    [Fact]
    public async Task The_window_follows_APEX_stopping_and_restarting_with_a_fresh_one_time_link()
    {
        var server = ApexServer();
        var rig = Build(new Uri(server.BaseUrl).Port);
        await rig.Vm.InitializeAsync();
        var first = rig.Vm.AssistantUrl;
        Assert.NotNull(first);

        await server.DisposeAsync(); // APEX arrêté : plus rien n'écoute, plus d'enregistrement
        Stopped(rig.Host);
        await rig.Vm.RefreshAsync();
        Assert.Null(rig.Vm.AssistantUrl); // jamais une page figée d'un APEX arrêté
        Assert.False(rig.Vm.IsAssistantHostVisible);
        Assert.True(rig.Vm.AssistantMessage.Contains("n'est pas démarré"), rig.Vm.AssistantMessage);
        Assert.True(rig.Vm.IsStartOfferedOnAssistant);

        await using var restarted = ApexServer(); // redémarré : autre processus, la session précédente n'existe plus
        Serving(rig.Host, new Uri(restarted.BaseUrl).Port, pid: 5151);
        await rig.Vm.RefreshAsync();
        Assert.NotNull(rig.Vm.AssistantUrl);
        Assert.NotEqual(first, rig.Vm.AssistantUrl);

        // Mesures suivantes, même processus : aucun nouveau lien (la page de l'utilisateur n'est pas rechargée).
        var current = rig.Vm.AssistantUrl;
        await rig.Vm.RefreshAsync();
        Assert.Equal(current, rig.Vm.AssistantUrl);
    }

    [Fact]
    public async Task An_overlay_hides_the_native_assistant_window_that_would_cover_it()
    {
        await using var server = ApexServer();
        var rig = Build(new Uri(server.BaseUrl).Port);
        await rig.Vm.InitializeAsync();
        Assert.True(rig.Vm.IsAssistantHostVisible);

        rig.Vm.ShowDiagnosticCommand.Execute(null);
        Assert.False(rig.Vm.IsAssistantHostVisible);
        rig.Vm.HideDiagnosticCommand.Execute(null);
        Assert.True(rig.Vm.IsAssistantHostVisible);

        rig.Vm.ShowControlCommand.Execute(null);
        Assert.False(rig.Vm.IsAssistantHostVisible);
        Assert.True(rig.Vm.IsControlPage);
        await rig.Vm.ShowAssistantCommand.ExecuteAsync();
        Assert.True(rig.Vm.IsAssistantHostVisible);
    }

    [Fact]
    public async Task Without_the_display_component_the_assistant_says_why_and_uses_the_browser()
    {
        await using var server = ApexServer();
        var rig = Build(new Uri(server.BaseUrl).Port);
        await rig.Vm.InitializeAsync();

        rig.Vm.ReportEmbeddedFailure("le composant Microsoft Edge WebView2 n'est pas installé sur ce PC");

        Assert.False(rig.Vm.IsEmbeddedAssistant);
        Assert.Null(rig.Vm.AssistantUrl);
        Assert.False(rig.Vm.IsAssistantHostVisible);
        Assert.Contains("WebView2 n'est pas installé", rig.Vm.AssistantMessage);
        Assert.Contains("go.microsoft.com/fwlink/p/?LinkId=2124703", rig.Vm.AssistantMessage);
        Assert.Contains(rig.Log.Entries, e => e.Message.Contains("Affichage intégré de l'assistant impossible"));

        await rig.Vm.OpenUiCommand.ExecuteAsync();
        Assert.Single(rig.Browser.Opened);
        await rig.Vm.RefreshAsync();
        Assert.Null(rig.Vm.AssistantUrl); // jamais réactivé en silence
    }

    [Fact]
    public async Task Outside_Windows_or_on_request_the_panel_keeps_the_browser()
    {
        await using var server = ApexServer();
        var rig = Build(new Uri(server.BaseUrl).Port, embedded: false);
        await rig.Vm.InitializeAsync();

        Assert.False(rig.Vm.IsAssistantPage);
        Assert.Null(rig.Vm.AssistantUrl);
        Assert.Empty(rig.Browser.Opened); // rien n'est ouvert sans un clic
        await rig.Vm.OpenUiCommand.ExecuteAsync();
        Assert.Single(rig.Browser.Opened);
    }
}
