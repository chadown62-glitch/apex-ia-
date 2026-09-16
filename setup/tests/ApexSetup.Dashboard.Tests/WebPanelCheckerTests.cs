using System.Net;
using System.Net.Sockets;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class WebPanelCheckerTests
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
    public async Task Reports_Running_after_a_real_response_from_the_configured_port()
    {
        await using var server = new LocalHttpTestServer(ctx =>
        {
            ctx.Response.StatusCode = 200;
            ctx.Response.Close();
            return Task.CompletedTask;
        });
        var checker = new WebPanelChecker(new HttpClient());
        var settings = new DashboardSettings { WebPanelPort = new Uri(server.BaseUrl).Port };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Running, state.Status);
        Assert.Equal(ComponentKey.WebPanel, state.Key);
    }

    [Fact]
    public async Task Reports_Stopped_when_no_dev_server_is_listening()
    {
        var checker = new WebPanelChecker(new HttpClient());
        // Port distinct, jamais lié — un vrai `npm run dev` non démarré, pas simulé.
        var settings = new DashboardSettings { WebPanelPort = FreeLoopbackPortWithNoListener() };

        var state = await checker.CheckAsync(settings);

        Assert.Equal(ComponentStatus.Stopped, state.Status);
    }
}
