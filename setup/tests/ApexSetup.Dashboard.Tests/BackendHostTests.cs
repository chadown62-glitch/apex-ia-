using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// MOCK TEST : <c>wsl.exe</c> n'existe pas dans cet environnement ; ces tests vérifient la LIGNE DE
/// COMMANDE produite (jamais le shell, jamais de démarrage de distribution) et l'interprétation des
/// sorties documentées de <c>wsl.exe</c>. Ils ne prouvent pas le comportement d'un vrai WSL.
/// </summary>
public class WslBackendHostTests
{
    private const string ListOutput = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n  Debian    Stopped         2\n";

    [Fact]
    public async Task wsl_exe_missing_is_WSL_not_installed()
    {
        var host = new WslBackendHost(new FakeProcessRunner());

        var probe = await host.ProbeAsync(null);

        Assert.Equal((ComponentStatus.Error, false, NotReadyCause.WslNotInstalled), (probe.Status, probe.Running, probe.Cause));
    }

    [Fact]
    public async Task Default_running_distribution_is_READY_and_readable()
    {
        var host = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", FakeProcessRunner.Ok(ListOutput)));

        var probe = await host.ProbeAsync(null);

        Assert.Equal((ComponentStatus.Ready, true, "Ubuntu"), (probe.Status, probe.Running, probe.Distro));
    }

    [Fact]
    public async Task A_stopped_preferred_distribution_is_OFFLINE_and_not_readable()
    {
        var host = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", FakeProcessRunner.Ok(ListOutput)));

        var probe = await host.ProbeAsync("debian");

        Assert.Equal((ComponentStatus.Offline, false, NotReadyCause.WslStopped), (probe.Status, probe.Running, probe.Cause));
    }

    [Fact]
    public async Task UTF16_output_of_wsl_exe_is_understood()
    {
        var utf16 = string.Join("\0", ListOutput.ToCharArray()) + "\0";
        var host = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", FakeProcessRunner.Ok(utf16)));

        Assert.True((await host.ProbeAsync(null)).Running);
    }

    [Fact]
    public async Task No_distribution_and_unknown_distribution_and_timeout_are_distinguished()
    {
        var none = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", FakeProcessRunner.Exit(1, "Windows Subsystem for Linux has no installed distributions.")));
        var unknown = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", FakeProcessRunner.Ok(ListOutput)));
        var timeout = new WslBackendHost(new FakeProcessRunner().WithRunResponse("-l -v", new ProcessRunResult { Started = true, TimedOut = true }));

        Assert.Equal(NotReadyCause.WslNotInstalled, (await none.ProbeAsync(null)).Cause);
        Assert.Contains("introuvable", (await unknown.ProbeAsync("Arch")).Detail);
        Assert.Equal((ComponentStatus.Unknown, NotReadyCause.WslUnresponsive), ((await timeout.ProbeAsync(null)).Status, (await timeout.ProbeAsync(null)).Cause));
    }

    [Fact]
    public void Reads_use_exec_argv_never_the_default_shell()
    {
        var home = WslBackendHost.BuildArgs("Ubuntu", homeDirectory: true, ".apex/runtime/backend.json");
        var absolute = WslBackendHost.BuildArgs(null, homeDirectory: false, "/home/x/apex/.apex/api-token");

        Assert.Equal(["-d", "Ubuntu", "--cd", "~", "--exec", "cat", "--", ".apex/runtime/backend.json"], home);
        Assert.Equal(["--exec", "cat", "--", "/home/x/apex/.apex/api-token"], absolute);
    }

    [Theory]
    [InlineData("/home/x/$(reboot)/t")]
    [InlineData("/home/x/`id`/t")]
    [InlineData("/home/x;rm -rf ~/t")]
    public async Task A_hostile_path_stays_a_single_argument(string path)
    {
        var runner = new FakeProcessRunner().WithRunResponse("--exec", FakeProcessRunner.Exit(1, "cat: No such file or directory"));
        var host = new WslBackendHost(runner);

        var read = await host.ReadFileAsync(null, path);

        Assert.Equal(FileReadKind.NotFound, read.Kind);
        var args = runner.RunInvocations.Single().Arguments;
        Assert.Equal(path, args[^1]);
        Assert.DoesNotContain("-c", args);
        Assert.DoesNotContain("bash", args);
        Assert.DoesNotContain("sh", args);
    }

    [Theory]
    [InlineData("relative")]
    [InlineData("/a/../etc/shadow")]
    [InlineData("-n")]
    [InlineData("/a/b\n/c")]
    public async Task Unsafe_absolute_paths_are_refused_before_any_process(string path)
    {
        var runner = new FakeProcessRunner();

        var read = await new WslBackendHost(runner).ReadFileAsync(null, path);

        Assert.Equal(FileReadKind.Failed, read.Kind);
        Assert.Empty(runner.RunInvocations);
    }

    [Fact]
    public async Task Missing_file_and_other_failures_are_distinguished()
    {
        var missing = new WslBackendHost(new FakeProcessRunner().WithRunResponse("--exec", FakeProcessRunner.Exit(1, "cat: x: No such file or directory")));
        var denied = new WslBackendHost(new FakeProcessRunner().WithRunResponse("--exec", FakeProcessRunner.Exit(1, "cat: x: Permission denied")));

        Assert.Equal(FileReadKind.NotFound, (await missing.ReadFileAsync(null, "/x")).Kind);
        Assert.Equal(FileReadKind.Failed, (await denied.ReadFileAsync(null, "/x")).Kind);
    }
}

/// <summary>Hôte Linux natif : vraies lectures sur le système de fichiers.</summary>
public class LocalBackendHostTests : IDisposable
{
    private readonly string _home = Path.Combine(Path.GetTempPath(), "apex-home-" + Guid.NewGuid());

    [Fact]
    public async Task Reads_home_relative_and_absolute_files_for_real()
    {
        Directory.CreateDirectory(Path.Combine(_home, ".apex", "runtime"));
        await File.WriteAllTextAsync(Path.Combine(_home, BackendDiscovery.RelativePath), "contenu");
        var host = new LocalBackendHost(_home);

        Assert.Equal("contenu", (await host.ReadHomeFileAsync(null, BackendDiscovery.RelativePath)).Content);
        Assert.Equal(FileReadKind.NotFound, (await host.ReadHomeFileAsync(null, ".apex/absent")).Kind);
        Assert.Equal(FileReadKind.NotFound, (await host.ReadFileAsync(null, _home + "/absent")).Kind);
        Assert.Equal(FileReadKind.Failed, (await host.ReadHomeFileAsync(null, "../etc/passwd")).Kind);
        Assert.False((await host.ProbeAsync(null)).Applicable);
    }

    public void Dispose()
    {
        if (Directory.Exists(_home))
        {
            Directory.Delete(_home, recursive: true);
        }
    }
}

public class PanelStatusServiceTests
{
    [Fact]
    public async Task A_stopped_WSL_is_never_touched()
    {
        var host = new FakeBackendHost { Probe = PanelData.WslStopped() };

        var snapshot = await new PanelStatusService(host, new StatusClient(new HttpClient())).MeasureAsync(new DashboardSettings());

        Assert.Empty(host.Reads); // aucune lecture => aucune commande qui démarrerait la distribution
        Assert.Equal(NotReadyCause.WslStopped, snapshot.Cause);
    }

    [Fact]
    public async Task Reads_discovery_then_process_then_token_and_reports_the_secret_for_masking()
    {
        var host = new FakeBackendHost { Probe = PanelData.WslRunning() };
        host.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: 1)); // port 1 : rien n'y écoute
        host.Files["/proc/4242/cmdline"] = FileRead.Ok("python\0-m\0apex.cli.main\0serve");
        host.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
        var secrets = new List<string>();
        var service = new PanelStatusService(host, new StatusClient(new HttpClient()), () => PanelData.Now);
        service.SecretRead += secrets.Add;

        var snapshot = await service.MeasureAsync(new DashboardSettings());

        Assert.Equal(["~/" + BackendDiscovery.RelativePath, "/proc/4242/cmdline", PanelData.ProjectRoot + "/.apex/api-token"], host.Reads);
        Assert.Equal([PanelData.Token], secrets);
        Assert.Equal(1, snapshot.Backend.Port);
        Assert.NotEqual(PanelVerdict.Ready, snapshot.Verdict);
        Assert.Equal(ComponentStatus.Error, snapshot.Get(ComponentKey.Backend)!.Status); // vivant, mais muet depuis 5 min
    }

    [Fact]
    public async Task Without_registration_the_default_or_configured_port_is_probed()
    {
        var host = new FakeBackendHost();

        var snapshot = await new PanelStatusService(host, new StatusClient(new HttpClient())).MeasureAsync(new DashboardSettings { ApiPort = 1 });

        Assert.Equal(1, snapshot.Backend.Port);
        Assert.Equal(NotReadyCause.BackendNotStarted, snapshot.Cause);
    }
}
