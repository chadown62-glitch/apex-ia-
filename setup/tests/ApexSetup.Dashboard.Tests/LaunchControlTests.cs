using System.Net;
using System.Text;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// « Démarrer APEX » / « Arrêter APEX » / « Ouvrir APEX » : le Panel n'exécute QUE le launcher officiel
/// (launch.sh, Startup Gate compris), sous une forme exacte, seulement sur un geste de l'utilisateur,
/// depuis un dossier qui ne peut pas venir d'un fichier non vérifié ; un refus du Startup Gate est
/// BLOCKED, jamais contourné ; l'interface s'ouvre par un lien à usage unique, sans jamais le jeton.
/// </summary>
public class LaunchControlTests
{
    private sealed class RecordingStarter : IDetachedProcessStarter
    {
        public List<(string Executable, IReadOnlyList<string> Arguments)> Starts { get; } = [];
        public TaskCompletionSource<int> Exit { get; } = new();

        public DetachedProcess Start(string executable, IReadOnlyList<string> arguments)
        {
            Starts.Add((executable, arguments));
            return new DetachedProcess(true, null, Exit.Task);
        }
    }

    private sealed class RecordingBrowser : IBrowserLauncher
    {
        public List<string> Opened { get; } = [];

        public string? Open(string url)
        {
            Opened.Add(url);
            return null;
        }
    }

    private sealed record Rig(
        MainWindowViewModel Vm, FakeBackendHost Host, RecordingStarter Starter, FakeProcessRunner Runner,
        RecordingBrowser Browser, InMemoryLogSink Log, JsonSettingsStore Store);

    private static Rig Build(DashboardSettings? settings = null, Action<FakeBackendHost>? configure = null, int port = 1)
    {
        var host = new FakeBackendHost();
        configure?.Invoke(host);
        var redactor = new Redactor();
        var log = new InMemoryLogSink(redactor);
        var client = new StatusClient(new HttpClient());
        var service = new PanelStatusService(host, client);
        service.SecretRead += redactor.Register;
        var store = new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "apex-launch-" + Guid.NewGuid() + ".json"));
        store.SaveAsync((settings ?? new DashboardSettings()) with { ApiPort = port }).GetAwaiter().GetResult();
        var starter = new RecordingStarter();
        var runner = new FakeProcessRunner().WithRunResponse("stop", FakeProcessRunner.Ok("backend APEX arrêté."));
        var browser = new RecordingBrowser();
        var control = new PanelControl(new BackendLauncher(starter, runner, wsl: true), client, browser);
        var vm = new MainWindowViewModel(store, service, log, redactor,
            problem => new DiagnosticContext("OS", ".NET", "/install", "/settings.json", "/logs", null, problem), null, control);
        return new Rig(vm, host, starter, runner, browser, log, store);
    }

    private static string RefusedJson(int code, string stage, string[] blocking, string[]? unverified = null) =>
        System.Text.Json.JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["schema"] = 1, ["service"] = "apex", ["phase"] = "refused", ["apex_version"] = "0.2.2", ["api_version"] = "v1",
            ["panel_protocol"] = 1, ["project_root"] = PanelData.ProjectRoot, ["token_file"] = ".apex/api-token",
            ["host"] = "127.0.0.1", ["port"] = 8000, ["pid"] = 777, ["started_at"] = PanelData.Now.AddMinutes(-2).ToString("o"),
            ["refusal"] = new Dictionary<string, object>
            {
                ["exit_code"] = code, ["stage"] = stage, ["blocking"] = blocking, ["unverified_blocking"] = unverified ?? [],
                ["at"] = PanelData.Now.ToString("o"),
            },
        });

    // -- forme exacte de ce qui est exécuté --------------------------------------------------------------

    [Theory]
    [InlineData("apex", null, new[] { "--cd", "~", "--exec", "bash", "apex/scripts/apex-launch.sh", "panel-serve" })]
    [InlineData("~/apex/", null, new[] { "--cd", "~", "--exec", "bash", "apex/scripts/apex-launch.sh", "panel-serve" })]
    [InlineData("/home/moi/mon apex", "Ubuntu-24.04",
        new[] { "-d", "Ubuntu-24.04", "--cd", "~", "--exec", "bash", "/home/moi/mon apex/scripts/apex-launch.sh", "panel-serve" })]
    public void Start_runs_the_official_launcher_with_direct_arguments(string root, string? distro, string[] expected)
    {
        var command = new BackendLauncher(new RecordingStarter(), new FakeProcessRunner(), wsl: true).Command(distro, root, "panel-serve");

        Assert.NotNull(command);
        Assert.Equal("wsl.exe", command.Value.Executable);
        Assert.Equal(expected, command.Value.Arguments);
    }

    [Theory]
    [InlineData("../etc")]
    [InlineData("apex/../../etc")]
    [InlineData("-rf")]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("apex\nrm")]
    public void A_dangerous_project_root_is_refused_and_nothing_runs(string root)
    {
        var starter = new RecordingStarter();
        var launcher = new BackendLauncher(starter, new FakeProcessRunner(), wsl: true);

        var (attempt, _) = launcher.Start(null, root, PanelData.Now);

        Assert.Null(launcher.Command(null, root, "panel-serve"));
        Assert.Empty(starter.Starts);
        Assert.False(attempt.Running);
        Assert.Contains("dossier APEX refusé", attempt.FailureReason);
    }

    [Fact]
    public void A_project_root_with_shell_characters_stays_one_argument()
    {
        var command = new BackendLauncher(new RecordingStarter(), new FakeProcessRunner(), wsl: true).Command(null, "apex;rm -rf ~", "panel-serve");

        Assert.Contains("apex;rm -rf ~/scripts/apex-launch.sh", command!.Value.Arguments); // un seul argv, jamais interprété
    }

    [Fact]
    public void On_a_linux_host_the_launcher_runs_directly()
    {
        var command = new BackendLauncher(new RecordingStarter(), new FakeProcessRunner(), wsl: false, localHome: "/home/moi").Command(null, "apex", "stop");

        Assert.Equal("bash", command!.Value.Executable);
        Assert.Equal(["/home/moi/apex/scripts/apex-launch.sh", "stop"], command.Value.Arguments);
    }

    // -- jamais sans un geste --------------------------------------------------------------------------------

    [Fact]
    public async Task Measuring_never_starts_anything_even_when_APEX_is_not_running()
    {
        var rig = Build();

        await rig.Vm.InitializeAsync();
        await rig.Vm.RefreshAsync();

        Assert.Equal(NotReadyCause.BackendNotStarted, rig.Vm.Snapshot!.Cause);
        Assert.True(rig.Vm.CanStart);
        Assert.Empty(rig.Starter.Starts);
        Assert.Empty(rig.Runner.RunInvocations);
    }

    [Fact]
    public async Task Start_runs_launch_sh_once_then_shows_STARTING_and_cannot_be_clicked_twice()
    {
        var rig = Build(new DashboardSettings { WslDistro = "Ubuntu-24.04" });
        await rig.Vm.InitializeAsync();

        await rig.Vm.StartCommand.ExecuteAsync();
        await rig.Vm.StartCommand.ExecuteAsync();

        var start = Assert.Single(rig.Starter.Starts);
        Assert.Equal(["-d", "Ubuntu-24.04", "--cd", "~", "--exec", "bash", "apex/scripts/apex-launch.sh", "panel-serve"], start.Arguments);
        Assert.True(rig.Vm.Launch!.Running);
        Assert.Equal(NotReadyCause.BackendStarting, rig.Vm.Snapshot!.Cause);
        Assert.False(rig.Vm.CanStart);
        Assert.True(rig.Vm.CanStop);
        Assert.Contains(rig.Log.Entries, e => e.Message.Contains("Startup Gate"));
    }

    [Fact]
    public async Task A_launch_that_ends_without_a_record_says_why()
    {
        var rig = Build();
        await rig.Vm.InitializeAsync();
        await rig.Vm.StartCommand.ExecuteAsync();

        rig.Starter.Exit.SetResult(127);
        for (var i = 0; i < 100 && rig.Vm.Snapshot!.Cause != NotReadyCause.StartFailed; i++)
        {
            await Task.Delay(20);
        }

        Assert.Equal(NotReadyCause.StartFailed, rig.Vm.Snapshot!.Cause);
        Assert.Contains("Paramètres", rig.Vm.Guidance);
        Assert.True(rig.Vm.CanStart); // corriger le dossier puis réessayer
    }

    [Theory]
    [InlineData(1, ".env")]
    [InlineData(3, "Python")]
    [InlineData(8, "déjà en cours")]
    public void Launcher_exit_codes_are_explained(int code, string expected)
    {
        var failure = StatusEngine.LaunchFailure(new LaunchAttempt(PanelData.Now, "apex", ExitCode: code));

        Assert.NotNull(failure);
        Assert.Contains(expected, failure.Value.Guidance);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(130)]
    [InlineData(143)]
    public void A_clean_or_requested_stop_is_never_shown_as_a_failure(int code) =>
        Assert.Null(StatusEngine.LaunchFailure(new LaunchAttempt(PanelData.Now, "apex", ExitCode: code)));

    // -- dossier : jamais choisi par un fichier non vérifié (S04) ----------------------------------------------

    [Fact]
    public async Task An_unverified_discovery_record_never_chooses_the_folder_that_is_started()
    {
        // Enregistrement présent mais aucun backend ne répond avec son jeton : « arrêté ». Son dossier
        // (/home/utilisateur/apex) n'est PAS vérifié : « Démarrer » utilise les Paramètres / le défaut.
        var rig = Build(configure: h =>
        {
            h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: 1));
            h.Files["/proc/4242/cmdline"] = FileRead.NotFound("absent");
        });
        await rig.Vm.InitializeAsync();
        Assert.Equal(NotReadyCause.BackendStopped, rig.Vm.Snapshot!.Cause);

        await rig.Vm.StartCommand.ExecuteAsync();

        Assert.Contains("apex/scripts/apex-launch.sh", Assert.Single(rig.Starter.Starts).Arguments);
        Assert.DoesNotContain(rig.Starter.Starts[0].Arguments, a => a.Contains(PanelData.ProjectRoot));
    }

    [Fact]
    public async Task The_folder_of_a_backend_that_answered_with_its_token_is_remembered()
    {
        await using var server = ApexServer();
        var port = new Uri(server.BaseUrl).Port;
        var rig = Build(port: port, configure: h =>
        {
            h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: port));
            h.Files["/proc/4242/cmdline"] = FileRead.Ok("python\0-m\0apex.cli.main\0serve");
            h.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
        });

        await rig.Vm.InitializeAsync();

        Assert.Equal(PanelVerdict.Ready, rig.Vm.Verdict);
        var saved = (await rig.Store.LoadAsync()).Settings;
        Assert.Equal(PanelData.ProjectRoot, saved.LastProjectRoot);
        Assert.Equal((PanelData.ProjectRoot, "dernier dossier où APEX a tourné"), saved.EffectiveProjectRoot());
        Assert.False(rig.Vm.CanStart);
        Assert.True(rig.Vm.CanStop && rig.Vm.CanOpen);
    }

    [Fact]
    public void A_folder_set_by_the_user_wins()
    {
        var settings = new DashboardSettings { ProjectRoot = "/opt/apex", LastProjectRoot = "/home/x/apex" };

        Assert.Equal(("/opt/apex", "Paramètres"), settings.EffectiveProjectRoot());
        Assert.Equal(("apex", "par défaut (~/apex)"), new DashboardSettings().EffectiveProjectRoot());
    }

    // -- Startup Gate : BLOCKED, jamais contourné ------------------------------------------------------------

    [Fact]
    public async Task A_startup_gate_refusal_is_BLOCKED_with_the_checks_and_can_be_retried_after_a_fix()
    {
        var rig = Build(configure: h => h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(RefusedJson(5, "gate", ["GATE-21"])));

        await rig.Vm.InitializeAsync();

        Assert.Equal(PanelVerdict.Blocked, rig.Vm.Verdict);
        Assert.Equal("APEX BLOCKED", rig.Vm.Headline);
        Assert.Equal(NotReadyCause.StartupRefused, rig.Vm.Snapshot!.Cause);
        Assert.Contains("Startup Gate", rig.Vm.Guidance);
        Assert.Contains("GATE-21", rig.Vm.Guidance);
        Assert.DoesNotContain("phase 2", rig.Vm.Guidance, StringComparison.OrdinalIgnoreCase);
        Assert.True(rig.Vm.CanStart);
        Assert.False(rig.Vm.CanOpen);
        Assert.Empty(rig.Starter.Starts);
    }

    [Fact]
    public void An_unverified_precondition_is_BLOCKED_and_named()
    {
        var s = PanelData.Decide(PanelData.WslRunning(), BackendDiscovery.Parse(RefusedJson(6, "gate", [], ["GATE-03"])),
            new StatusFetch(FetchOutcome.Refused, null, null, "refusé"));

        Assert.Equal(PanelVerdict.Blocked, s.Verdict);
        Assert.Contains("précondition", s.Guidance);
        Assert.Contains("GATE-03", s.Guidance);
    }

    [Fact]
    public void A_docker_failure_is_NOT_READY_not_BLOCKED()
    {
        var s = PanelData.Decide(PanelData.WslRunning(), BackendDiscovery.Parse(RefusedJson(2, "infrastructure", [])),
            new StatusFetch(FetchOutcome.Refused, null, null, "refusé"));

        Assert.Equal(PanelVerdict.NotReady, s.Verdict);
        Assert.Equal(NotReadyCause.StartFailed, s.Cause);
        Assert.Contains("Docker", s.Guidance);
    }

    [Fact]
    public void A_running_backend_always_wins_over_an_old_refusal()
    {
        var s = PanelData.Decide(PanelData.WslRunning(), BackendDiscovery.Parse(RefusedJson(5, "gate", ["GATE-21"])),
            PanelData.Fetch(PanelData.StatusJson()), PanelData.GoodToken(), alive: true);

        Assert.Equal(PanelVerdict.Ready, s.Verdict);
    }

    [Theory]
    [InlineData("\"refusal\": {\"exit_code\": 5, \"stage\": \"gate\", \"blocking\": [\"GATE 21; rm\"], \"unverified_blocking\": []}")]
    [InlineData("\"refusal\": {\"exit_code\": 5, \"stage\": \"bypass\", \"blocking\": [], \"unverified_blocking\": []}")]
    [InlineData("\"refusal\": {\"exit_code\": 0, \"stage\": \"gate\", \"blocking\": [], \"unverified_blocking\": []}")]
    [InlineData("\"no_refusal\": 1")]
    public void A_malformed_refusal_is_an_invalid_record(string refusal)
    {
        var json = RefusedJson(5, "gate", []);
        json = json[..json.IndexOf("\"refusal\"", StringComparison.Ordinal)] + refusal + "}";

        Assert.Equal(DiscoveryKind.Invalid, BackendDiscovery.Parse(json).Kind);
    }

    // -- arrêter -----------------------------------------------------------------------------------------------

    [Fact]
    public async Task Stop_runs_launch_sh_stop_and_a_requested_stop_is_not_a_failure()
    {
        var rig = Build();
        await rig.Vm.InitializeAsync();
        await rig.Vm.StartCommand.ExecuteAsync();

        await rig.Vm.StopCommand.ExecuteAsync();
        rig.Starter.Exit.SetResult(130);
        await Task.Delay(50);
        await rig.Vm.RefreshAsync();

        var stop = Assert.Single(rig.Runner.RunInvocations);
        Assert.Equal(["--cd", "~", "--exec", "bash", "apex/scripts/apex-launch.sh", "stop"], stop.Arguments);
        Assert.Null(rig.Vm.Launch);
        Assert.Equal(NotReadyCause.BackendNotStarted, rig.Vm.Snapshot!.Cause);
        Assert.Equal("APEX arrêté.", rig.Vm.ActionMessage);
    }

    // -- ouvrir l'interface ------------------------------------------------------------------------------------

    private static LocalHttpTestServer ApexServer(string code = "c0de_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345") => new(async ctx =>
    {
        var path = ctx.Request.Url!.AbsolutePath;
        var authorized = ctx.Request.Headers["Authorization"] == $"Bearer {PanelData.Token}";
        var (status, body) = !authorized ? (401, "{\"detail\":\"jeton invalide\"}")
            : path == "/api/v1/status" ? (200, PanelData.StatusJson())
            : path == "/api/v1/ui/launch-code" && ctx.Request.HttpMethod == "POST"
                ? (200, $"{{\"status\":\"real\",\"data\":{{\"login_path\":\"/ui/login?code={code}\",\"expires_in_s\":60,\"ui_available\":true,\"ui_detail\":\"ok\"}},\"detail\":null}}")
            : (404, "{\"detail\":\"Not Found\"}");
        ctx.Response.StatusCode = status;
        var bytes = Encoding.UTF8.GetBytes(body);
        ctx.Response.ContentType = "application/json";
        await ctx.Response.OutputStream.WriteAsync(bytes);
        ctx.Response.Close();
    });

    [Fact]
    public async Task Open_asks_a_one_time_code_and_opens_the_local_interface_without_the_token()
    {
        await using var server = ApexServer();
        var port = new Uri(server.BaseUrl).Port;
        var rig = Build(port: port, configure: h =>
        {
            h.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: port));
            h.Files["/proc/4242/cmdline"] = FileRead.Ok("python\0-m\0apex.cli.main\0serve");
            h.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
        });
        await rig.Vm.InitializeAsync();

        await rig.Vm.OpenUiCommand.ExecuteAsync();

        var url = Assert.Single(rig.Browser.Opened);
        Assert.Equal($"http://127.0.0.1:{port}/ui/login?code=c0de_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345", url);
        Assert.DoesNotContain(PanelData.Token, url);
        Assert.DoesNotContain(rig.Log.Entries, e => e.Message.Contains("code="));
        Assert.Contains("ouverte", rig.Vm.ActionMessage);
    }

    [Fact]
    public async Task A_malicious_launch_code_answer_is_never_opened()
    {
        await using var server = ApexServer(code: "x&next=http://evil.example");
        var (host, port) = (new Uri(server.BaseUrl).Host, new Uri(server.BaseUrl).Port);

        var launch = await new StatusClient(new HttpClient()).CreateLaunchCodeAsync(host, port, PanelData.Token);

        Assert.False(launch.Ok);
        Assert.Null(launch.Url);
    }

    [Theory]
    [InlineData("http://127.0.0.1:8000/ui/login?code=abc", true)]
    [InlineData("http://[::1]:8000/ui/login?code=abc", true)]
    [InlineData("http://evil.example/ui/login?code=abc", false)]
    [InlineData("https://127.0.0.1:8000/ui/login?code=abc", false)]
    [InlineData("file:///C:/Windows/System32/calc.exe", false)]
    [InlineData("http://127.0.0.1:8000/api/v1/status", false)]
    public void Only_the_local_interface_login_can_be_opened(string url, bool allowed) =>
        Assert.Equal(allowed, SystemBrowserLauncher.IsLocalHttp(url));

    [Fact]
    public async Task Without_an_authenticated_backend_nothing_can_be_opened()
    {
        var rig = Build();
        await rig.Vm.InitializeAsync();

        Assert.False(rig.Vm.CanOpen);
        await rig.Vm.OpenUiCommand.ExecuteAsync();
        Assert.Empty(rig.Browser.Opened);
    }

    [Fact]
    public void Exit_codes_include_BLOCKED() =>
        Assert.Equal((0, 1, 2, 3, 4), (Program.ExitCode(PanelVerdict.Ready), Program.ExitCode(PanelVerdict.Degraded),
            Program.ExitCode(PanelVerdict.NotReady), Program.ExitCode(PanelVerdict.VersionMismatch), Program.ExitCode(PanelVerdict.Blocked)));
}
