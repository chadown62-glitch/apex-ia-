using ApexSetup.Dashboard.Models;
using System.Net;
using System.Net.Sockets;
using System.Text;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>Chaque issue HTTP est produite par un VRAI serveur local (HttpListener), jamais par un gestionnaire simulé.</summary>
public class StatusClientTests
{
    private static async Task Reply(HttpListenerContext ctx, int status, string body)
    {
        ctx.Response.StatusCode = status;
        var bytes = Encoding.UTF8.GetBytes(body);
        ctx.Response.ContentType = "application/json";
        await ctx.Response.OutputStream.WriteAsync(bytes);
        ctx.Response.Close();
    }

    private static (string Host, int Port) Address(LocalHttpTestServer server)
    {
        var uri = new Uri(server.BaseUrl);
        return (uri.Host, uri.Port);
    }

    [Fact]
    public async Task A_valid_APEX_response_is_Ok_and_the_bearer_token_is_sent()
    {
        string? authorization = null;
        await using var server = new LocalHttpTestServer(ctx =>
        {
            authorization = ctx.Request.Headers["Authorization"];
            return Reply(ctx, 200, PanelData.StatusJson());
        });
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token);

        Assert.Equal(FetchOutcome.Ok, fetch.Outcome);
        Assert.Equal(1, fetch.Payload!.PanelProtocol);
        Assert.Equal(1 + ComponentKeys.BackendServices.Count, fetch.Payload.Components.Count); // api + services mesurés
        Assert.Equal($"Bearer {PanelData.Token}", authorization);
        Assert.DoesNotContain(PanelData.Token, fetch.Detail);
    }

    [Fact]
    public async Task A_401_is_Unauthorized_and_never_quotes_the_token()
    {
        await using var server = new LocalHttpTestServer(ctx => Reply(ctx, 401, "{\"detail\":\"jeton invalide\"}"));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token);

        Assert.Equal(FetchOutcome.Unauthorized, fetch.Outcome);
        Assert.DoesNotContain(PanelData.Token, fetch.Detail);
    }

    [Fact]
    public async Task A_404_from_an_older_APEX_backend_is_OldBackend()
    {
        // Backend APEX antérieur : /status inconnu (404 FastAPI), /health protégé par le jeton (401 JSON).
        await using var server = new LocalHttpTestServer(ctx => ctx.Request.Url!.AbsolutePath.EndsWith("/health")
            ? Reply(ctx, 401, "{\"detail\":\"Authentification requise\"}")
            : Reply(ctx, 404, "{\"detail\":\"Not Found\"}"));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, null);

        Assert.Equal(FetchOutcome.OldBackend, fetch.Outcome);
    }

    [Fact]
    public async Task A_404_from_another_program_is_NotApex()
    {
        await using var server = new LocalHttpTestServer(ctx => Reply(ctx, 404, "<html>autre serveur</html>"));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, null);

        Assert.Equal(FetchOutcome.NotApex, fetch.Outcome);
    }

    [Theory]
    [InlineData("<html>page web</html>")]
    [InlineData("{\"data\":{\"service\":\"autre\"}}")]
    [InlineData("[1,2,3]")]
    public async Task A_200_that_is_not_the_APEX_status_is_NotApex(string body)
    {
        await using var server = new LocalHttpTestServer(ctx => Reply(ctx, 200, body));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token);

        Assert.Equal(FetchOutcome.NotApex, fetch.Outcome);
        Assert.Null(fetch.Payload);
    }

    [Fact]
    public async Task An_APEX_response_without_protocol_is_OldBackend()
    {
        await using var server = new LocalHttpTestServer(ctx => Reply(ctx, 200, "{\"status\":\"real\",\"data\":{\"service\":\"apex\",\"components\":[]}}"));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token);

        Assert.Equal(FetchOutcome.OldBackend, fetch.Outcome);
    }

    [Fact]
    public async Task A_500_is_HttpError_with_its_code()
    {
        await using var server = new LocalHttpTestServer(ctx => Reply(ctx, 500, "{\"detail\":\"boom\"}"));
        var (host, port) = Address(server);

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token);

        Assert.Equal((FetchOutcome.HttpError, 500), (fetch.Outcome, fetch.HttpStatus));
    }

    [Fact]
    public async Task Nothing_listening_is_Refused()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();

        var fetch = await new StatusClient(new HttpClient()).FetchAsync("127.0.0.1", port, PanelData.Token);

        Assert.Equal(FetchOutcome.Refused, fetch.Outcome);
    }

    [Fact]
    public async Task A_slow_backend_is_a_Timeout_within_the_bound()
    {
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            await Task.Delay(TimeSpan.FromSeconds(5));
            await Reply(ctx, 200, PanelData.StatusJson());
        });
        var (host, port) = Address(server);
        var watch = System.Diagnostics.Stopwatch.StartNew();

        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, port, PanelData.Token, TimeSpan.FromMilliseconds(300));

        Assert.Equal(FetchOutcome.Timeout, fetch.Outcome);
        Assert.True(watch.Elapsed < TimeSpan.FromSeconds(3));
    }

    [Theory]
    [InlineData("hôte invalide avec espaces")]
    [InlineData("")]
    public async Task A_corrupted_host_is_Failed_never_an_exception(string host)
    {
        var fetch = await new StatusClient(new HttpClient()).FetchAsync(host, 8000, null, TimeSpan.FromSeconds(1));

        Assert.NotEqual(FetchOutcome.Ok, fetch.Outcome);
    }
}

public class BackendDiscoveryTests
{
    [Fact]
    public void Parses_the_record_written_by_apex_serve()
    {
        var read = BackendDiscovery.Parse(PanelData.DiscoveryJson(port: 8123, pid: 77));

        Assert.Equal(DiscoveryKind.Found, read.Kind);
        Assert.Equal((8123, 77), (read.Record!.Port, read.Record.Pid));
        Assert.Equal("/home/utilisateur/apex/.apex/api-token", BackendDiscovery.TokenPath(read.Record));
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"service\":\"apex\"}")]
    [InlineData("[]")]
    [InlineData("pas du json")]
    [InlineData("")]
    public void Anything_incomplete_is_Invalid(string json) =>
        Assert.Equal(DiscoveryKind.Invalid, BackendDiscovery.Parse(json).Kind);

    [Theory]
    [InlineData("project_root", "relatif/sans/slash")]
    [InlineData("project_root", "/home/x/../../etc")]
    [InlineData("project_root", "-rf")]
    [InlineData("token_file", "../../etc/shadow")]
    [InlineData("token_file", "/etc/shadow")]
    [InlineData("token_file", "--help")]
    public void Tampered_paths_are_refused(string field, string value)
    {
        var json = System.Text.Json.Nodes.JsonNode.Parse(PanelData.DiscoveryJson())!;
        json[field] = value;

        Assert.Equal(DiscoveryKind.Invalid, BackendDiscovery.Parse(json.ToJsonString()).Kind);
    }

    [Theory]
    [InlineData("port", 0)]
    [InlineData("port", 70000)]
    [InlineData("pid", 0)]
    public void Out_of_range_numbers_are_refused(string field, int value)
    {
        var json = System.Text.Json.Nodes.JsonNode.Parse(PanelData.DiscoveryJson())!;
        json[field] = value;

        Assert.Equal(DiscoveryKind.Invalid, BackendDiscovery.Parse(json.ToJsonString()).Kind);
    }

    [Theory]
    [InlineData("0.0.0.0", "127.0.0.1")]
    [InlineData("localhost", "127.0.0.1")]
    [InlineData("127.0.0.1", "127.0.0.1")]
    [InlineData("192.168.1.20", "192.168.1.20")]
    public void Reachable_host_maps_wildcards_to_loopback(string listen, string reach) =>
        Assert.Equal(reach, BackendDiscovery.ReachableHost(listen));

    [Fact]
    public void Token_is_trimmed_and_never_shown()
    {
        var token = PanelData.GoodToken();

        Assert.Equal(TokenKind.Ok, token.Kind);
        Assert.Equal(PanelData.Token, token.Value);
        Assert.DoesNotContain(PanelData.Token, token.Detail);
        Assert.DoesNotContain(PanelData.Token, token.ToString());
    }

    [Theory]
    [InlineData("court")]
    [InlineData("contient des espaces au milieu du jeton")]
    [InlineData("")]
    public void Malformed_tokens_are_Invalid(string content) =>
        Assert.Equal(TokenKind.Invalid, TokenRead.FromFile(FileRead.Ok(content)).Kind);

    [Fact]
    public void Missing_token_file_is_Missing() =>
        Assert.Equal(TokenKind.Missing, TokenRead.FromFile(FileRead.NotFound("absent")).Kind);
}
