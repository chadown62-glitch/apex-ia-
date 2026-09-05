using System.Net;
using System.Net.Sockets;

namespace ApexSetup.Core.Tests.Fixtures;

/// <summary>
/// Serveur HTTP local RÉEL (boucle locale uniquement) pour les tests du
/// moteur de téléchargement — jamais un <c>HttpMessageHandler</c> simulé
/// seul. Chaque scénario réseau (reprise, coupure, timeout) est produit
/// par un comportement HTTP réel, observable par un vrai client.
/// </summary>
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
            catch (Exception) when (_stopping)
            {
                return;
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
            try
            {
                ctx.Response.Abort();
            }
            catch
            {
                // Le test a peut-être déjà fermé — sans conséquence.
            }
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
        try
        {
            await _acceptLoop.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
        }
        catch
        {
            // Best-effort à la fermeture du test.
        }
    }
}
