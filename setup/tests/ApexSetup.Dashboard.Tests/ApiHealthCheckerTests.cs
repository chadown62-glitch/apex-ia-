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
    public async Task Reports_Unknown_when_the_token_file_cannot_be_read_the_API_probably_never_started()
    {
        var runner = new FakeProcessRunner().WithRunResponse("api-token", new ProcessRunResult { Started = true, ExitCode = 1, Stderr = "No such file" });
        var checker = new ApiHealthChecker(runner, new HttpClient());
        var settings = new DashboardSettings { ProjectPathWsl = "/home/user/apex" };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Unknown, state.Status);
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
