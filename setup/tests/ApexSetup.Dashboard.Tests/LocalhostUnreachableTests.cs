using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Défaut W2 de l'audit : backend vivant qui écoute DANS WSL, port refusé depuis Windows. Les lignes de
/// <c>/proc/net/tcp</c> ci-dessous sont RÉELLES (noyau Linux du conteneur de développement : une socket
/// en écoute sur 127.0.0.1:48123 = 0x BBFB, puis une connexion établie sur le même port) ; le transport
/// <c>wsl.exe</c> reste un double (MOCK TEST).
/// </summary>
public class LocalhostUnreachableTests
{
    private const string Header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n";
    private const string Listening = "   0: 0100007F:BBFB 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 11304 1 0000000027ee004b 100 0 0 10 0\n";
    private const string Established = "   5: 0100007F:BBFB 0100007F:C008 01 00000000:00000000 00:00000000 00000000     0        0 11306 1 00000000758c1749 20 0 0 10 -1\n";

    [Fact]
    public void Only_a_listening_socket_on_the_port_counts()
    {
        Assert.True(ProcNetTcp.IsListening(Header + Established + Listening, 48123));
        Assert.False(ProcNetTcp.IsListening(Header + Established, 48123)); // connexion établie : pas une écoute
        Assert.False(ProcNetTcp.IsListening(Header + Listening, 48124));
        Assert.False(ProcNetTcp.IsListening(Header + "garbage line\n", 48123));
        Assert.False(ProcNetTcp.IsListening(null, 48123));
    }

    private static (FakeBackendHost Host, PanelStatusService Service) Build(string? tcp, string? tcp6)
    {
        var host = new FakeBackendHost { Probe = PanelData.WslRunning() };
        host.Files["~/" + BackendDiscovery.RelativePath] = FileRead.Ok(PanelData.DiscoveryJson(port: 1)); // rien n'écoute sur 1
        host.Files["/proc/4242/cmdline"] = FileRead.Ok("python\0-m\0apex\0serve\0");
        host.Files[PanelData.ProjectRoot + "/.apex/api-token"] = FileRead.Ok(PanelData.Token);
        host.Files["/proc/net/tcp"] = tcp is null ? FileRead.NotFound("absent") : FileRead.Ok(tcp);
        host.Files["/proc/net/tcp6"] = tcp6 is null ? FileRead.NotFound("absent") : FileRead.Ok(tcp6);
        return (host, new PanelStatusService(host, new StatusClient(new HttpClient()), () => PanelData.Now));
    }

    private const string ListeningOnPort1 = "   0: 0100007F:0001 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 1 1 0 100 0 0 10 0\n";

    [Fact]
    public async Task Alive_and_listening_in_WSL_but_refused_from_Windows_is_named_localhost_unreachable()
    {
        var (_, service) = Build(Header + ListeningOnPort1, tcp6: null); // tcp6 absente : IPv6 désactivé

        var snapshot = await service.MeasureAsync(new DashboardSettings());

        Assert.Equal((PanelVerdict.NotReady, NotReadyCause.LocalhostUnreachable), (snapshot.Verdict, snapshot.Cause));
        Assert.Equal(ComponentStatus.Ready, snapshot.Get(ComponentKey.Backend)!.Status);
        Assert.Equal(ComponentStatus.Error, snapshot.Get(ComponentKey.Api)!.Status);
        Assert.Contains("localhost", snapshot.Guidance);
        Assert.DoesNotContain("Arrêtez", snapshot.Guidance); // l'ancienne consigne fausse : arrêter un backend qui marche
    }

    [Fact]
    public async Task Alive_but_not_listening_keeps_the_previous_diagnosis()
    {
        var (_, service) = Build(Header + Established, tcp6: Header);

        var snapshot = await service.MeasureAsync(new DashboardSettings());

        Assert.Equal(NotReadyCause.BackendUnresponsive, snapshot.Cause);
    }

    [Fact]
    public async Task An_unreadable_table_never_concludes_localhost_unreachable()
    {
        var (host, service) = Build(tcp: null, tcp6: null);
        host.Files["/proc/net/tcp"] = FileRead.Failed("lecture refusée");

        var snapshot = await service.MeasureAsync(new DashboardSettings());

        Assert.NotEqual(NotReadyCause.LocalhostUnreachable, snapshot.Cause);
    }
}
