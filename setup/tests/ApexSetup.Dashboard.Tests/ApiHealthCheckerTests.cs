using System.Net;
using System.Net.Sockets;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class ApiHealthCheckerTests
{
    private static int FreeLoopbackPortWithNoListener()
    {
        using var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    [Fact]
    public async Task Reports_Unknown_when_the_project_path_is_not_configured()
    {
        var checker = new ApiHealthChecker(new FakeProcessRunner(), new HttpClient());

        var state = await checker.CheckAsync(new DashboardSettings());

        Assert.Equal(ComponentStatus.Unknown, state.Status);
    }

    [Fact]
    public async Task Reports_Running_after_a_real_200_from_health_with_the_token_read_from_WSL()
    {
        string? receivedAuth = null;
        await using var server = new LocalHttpTestServer(ctx =>
        {
            receivedAuth = ctx.Request.Headers["Authorization"];
            ctx.Response.StatusCode = 200;
            ctx.Response.Close();
            return Task.CompletedTask;
        });
        var port = new Uri(server.BaseUrl).Port;
        var runner = new FakeProcessRunner().WithRunResponse(
            "api-token", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = "tok3n\n" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex", ApiHost = "127.0.0.1", ApiPort = port };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Running, state.Status);
        Assert.Equal("Bearer tok3n", receivedAuth);
    }

    [Fact]
    public async Task Reports_Unknown_with_a_calm_message_when_the_token_file_does_not_exist_yet()
    {
        // Régression du bootstrap découvert en validation physique : `.apex/api-token`
        // n'existe pas encore juste après le lancement du backend — un état ATTENDU
        // (voir apex.api.app._lifespan, qui le crée avant d'accepter des connexions),
        // jamais un signal d'échec. Le message ne doit jamais laisser croire que l'API
        // "n'a probablement jamais démarré" dans ce cas précis.
        var runner = new FakeProcessRunner().WithRunResponse(
            "api-token", new ProcessRunResult { Started = true, ExitCode = 1, Stderr = "cat: .apex/api-token: No such file or directory" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex" };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Unknown, state.Status);
        Assert.Contains("pas encore créé", state.Detail);
        Assert.DoesNotContain("jamais démarré", state.Detail);
    }

    [Fact]
    public async Task Reports_Unknown_with_a_distinct_diagnostic_when_the_token_file_read_genuinely_fails()
    {
        // Différent du cas précédent : ici le fichier EXISTE probablement mais la
        // lecture échoue pour une autre raison (permissions, ...) — un diagnostic
        // différent, jamais confondu avec le cas "pas encore créé" ci-dessus.
        var runner = new FakeProcessRunner().WithRunResponse(
            "api-token", new ProcessRunResult { Started = true, ExitCode = 1, Stderr = "cat: .apex/api-token: Permission denied" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex" };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Unknown, state.Status);
        Assert.Contains("permissions", state.Detail);
        Assert.DoesNotContain("pas encore créé", state.Detail);
    }

    [Fact]
    public async Task Never_includes_any_token_looking_value_in_a_failure_diagnostic()
    {
        // Cas 6 (sécurité) : même si Stdout contenait accidentellement un fragment de
        // jeton (ex. lecture partielle), le message affiché à l'écran ne doit jamais
        // le reprendre — ici prouvé pour la voie d'échec, la seule où un contenu de
        // Stdout imprévu pourrait fuiter si le code le recopiait par erreur.
        const string suspiciousStdoutFragment = "leaked-token-fragment-should-never-appear";
        var runner = new FakeProcessRunner().WithRunResponse(
            "api-token", new ProcessRunResult { Started = true, ExitCode = 1, Stdout = suspiciousStdoutFragment, Stderr = "Permission denied" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex" };

        var state = await checker.CheckAsync(settings);

        Assert.DoesNotContain(suspiciousStdoutFragment, state.Detail);
    }

    [Fact]
    public async Task Recovers_to_Running_once_the_token_file_appears_after_initially_being_absent()
    {
        // Preuve directe du scénario "premier lancement" : le jeton n'existe pas encore
        // au premier sondage (backend tout juste lancé, apex.api.app._lifespan n'a pas
        // encore écrit .apex/api-token), puis apparaît — la même instance de checker,
        // interrogée à nouveau (exactement ce que fait ApexOrchestrator.PollUntilAsync),
        // doit détecter la transition sans aucune intervention humaine.
        await using var server = new LocalHttpTestServer(ctx =>
        {
            ctx.Response.StatusCode = 200;
            ctx.Response.Close();
            return Task.CompletedTask;
        });
        var port = new Uri(server.BaseUrl).Port;
        var callCount = 0;
        var runner = new FakeProcessRunner().WithRunResponse("api-token", () =>
        {
            callCount++;
            return callCount == 1
                ? new ProcessRunResult { Started = true, ExitCode = 1, Stderr = "cat: .apex/api-token: No such file or directory" }
                : new ProcessRunResult { Started = true, ExitCode = 0, Stdout = "real-token\n" };
        });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex", ApiHost = "127.0.0.1", ApiPort = port };

        var first = await checker.CheckAsync(settings);
        var second = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Unknown, first.Status);
        Assert.Contains("pas encore créé", first.Detail);
        Assert.Equal(ComponentStatus.Running, second.Status);
    }

    [Fact]
    public async Task Reports_Stopped_when_the_connection_is_refused_the_process_is_simply_not_running()
    {
        var runner = new FakeProcessRunner().WithRunResponse(
            "api-token", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = "tok3n\n" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex", ApiHost = "127.0.0.1", ApiPort = FreeLoopbackPortWithNoListener() };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Stopped, state.Status);
    }
}
