using System.Net;
using System.Net.Sockets;

namespace ApexSetup.Application.Tests.Fixtures;

/// <summary>Serveur HTTP local réel, même patron que ApexSetup.Core.Tests — dupliqué ici pour un artefact réellement téléchargeable dans les tests <c>install</c> bout en bout.</summary>
internal sealed class LocalHttpTestServer : IAsyncDisposable
{
    private readonly HttpListener _listener = new();
    private readonly Func<HttpListenerContext, Task> _handler;
    private readonly Task _acceptLoop;
    private volatile bool _stopping;

    public string BaseUrl { get; }

    public LocalHttpTestServer(Func<HttpListenerContext, Task> handler)
    {
        _handler = handler;
        var port = GetFreeLoopbackPort();
        BaseUrl = $"http://127.0.0.1:{port}/";
        _listener.Prefixes.Add(BaseUrl);
        _listener.Start();
        _acceptLoop = Task.Run(AcceptLoopAsync);
    }

    public Uri UrlFor(string path) => new(BaseUrl + path.TrimStart('/'));

    private async Task AcceptLoopAsync()
    {
        while (!_stopping)
        {
            HttpListenerContext ctx;
            try
            {
                ctx = await _listener.GetContextAsync().ConfigureAwait(false);
            }
            catch (Exception)
            {
                return;
            }
            _ = Task.Run(() => HandleSafelyAsync(ctx));
        }
    }

    private async Task HandleSafelyAsync(HttpListenerContext ctx)
    {
        try
        {
            await _handler(ctx).ConfigureAwait(false);
        }
        catch
        {
            try { ctx.Response.Abort(); } catch { /* déjà fermé côté test — sans conséquence */ }
        }
    }

    private static int GetFreeLoopbackPort()
    {
        var probe = new TcpListener(IPAddress.Loopback, 0);
        probe.Start();
        var port = ((IPEndPoint)probe.LocalEndpoint).Port;
        probe.Stop();
        return port;
    }

    public async ValueTask DisposeAsync()
    {
        _stopping = true;
        _listener.Stop();
        _listener.Close();
        try { await _acceptLoop.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false); } catch { /* best effort */ }
    }
}
