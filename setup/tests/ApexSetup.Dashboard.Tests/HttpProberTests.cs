using System.Net;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Mission §20 — API health check / Web Panel health check, contre un VRAI serveur HTTP local (boucle locale), jamais un handler HTTP simulé seul.</summary>
public class HttpProberTests
{
    [Fact]
    public async Task Reports_Ok_for_a_real_200_response()
    {
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            ctx.Response.StatusCode = 200;
            await ctx.Response.OutputStream.WriteAsync(new byte[0]);
            ctx.Response.Close();
        });
        using var client = new HttpClient();

        var result = await HttpProber.ProbeAsync(client, server.BaseUrl, bearerToken: null, TimeSpan.FromSeconds(5));

        Assert.True(result.IsOk);
        Assert.Equal(200, result.StatusCode);
    }

    [Fact]
    public async Task Sends_the_bearer_token_when_provided()
    {
        string? receivedAuth = null;
        await using var server = new LocalHttpTestServer(ctx =>
        {
            receivedAuth = ctx.Request.Headers["Authorization"];
            ctx.Response.StatusCode = 200;
            ctx.Response.Close();
            return Task.CompletedTask;
        });
        using var client = new HttpClient();

        await HttpProber.ProbeAsync(client, server.BaseUrl, bearerToken: "secret-token", TimeSpan.FromSeconds(5));

        Assert.Equal("Bearer secret-token", receivedAuth);
    }

    [Fact]
    public async Task Reports_HttpError_with_the_real_status_code_for_a_500()
    {
        await using var server = new LocalHttpTestServer(ctx =>
        {
            ctx.Response.StatusCode = 500;
            ctx.Response.Close();
            return Task.CompletedTask;
        });
        using var client = new HttpClient();

        var result = await HttpProber.ProbeAsync(client, server.BaseUrl, bearerToken: null, TimeSpan.FromSeconds(5));

        Assert.Equal(HttpProbeOutcome.HttpError, result.Outcome);
        Assert.Equal(500, result.StatusCode);
    }

    [Fact]
    public async Task Reports_ConnectionRefused_when_nothing_listens_on_the_port()
    {
        using var client = new HttpClient();
        var freePort = GetFreeLoopbackPortWithNoListener();

        var result = await HttpProber.ProbeAsync(client, $"http://127.0.0.1:{freePort}/", bearerToken: null, TimeSpan.FromSeconds(5));

        Assert.Equal(HttpProbeOutcome.ConnectionRefused, result.Outcome);
    }

    [Fact]
    public async Task Reports_Timeout_when_the_server_never_responds_within_the_bound()
    {
        var release = new TaskCompletionSource();
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            await release.Task; // ne répond jamais avant que le test ne libère explicitement (nettoyage, jamais un vrai blocage infini du test lui-même)
            ctx.Response.StatusCode = 200;
            ctx.Response.Close();
        });
        using var client = new HttpClient();

        var result = await HttpProber.ProbeAsync(client, server.BaseUrl, bearerToken: null, TimeSpan.FromMilliseconds(200));

        Assert.Equal(HttpProbeOutcome.Timeout, result.Outcome);
        release.TrySetResult();
    }

    [Fact]
    public async Task Reports_UnknownFailure_instead_of_throwing_for_an_unparsable_URL()
    {
        // Régression du BUG RÉEL (validation physique N+31, voir HttpProber.ProbeAsync) :
        // un `ApiHost` vidé dans les Paramètres produit une URL comme "http://:8000/health",
        // que `new HttpRequestMessage(Get, url)` refuse avec `UriFormatException` — non
        // interceptée auparavant, donc un crash réel remontant jusqu'au ViewModel.
        using var client = new HttpClient();

        var result = await HttpProber.ProbeAsync(client, "http://:8000/health", bearerToken: null, TimeSpan.FromSeconds(5));

        Assert.Equal(HttpProbeOutcome.UnknownFailure, result.Outcome);
        Assert.Null(result.StatusCode);
    }

    private static int GetFreeLoopbackPortWithNoListener()
    {
        using var probe = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }
}
