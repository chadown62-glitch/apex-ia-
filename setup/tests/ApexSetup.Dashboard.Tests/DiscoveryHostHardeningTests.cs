using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Sécurité (audit Windows réel) : un enregistrement de découverte altéré ne doit jamais faire envoyer le
/// jeton de l'API à une autre machine. Seules les adresses d'écoute locales sont acceptées.
/// </summary>
public class DiscoveryHostHardeningTests
{
    private static string WithHost(string host) =>
        PanelData.DiscoveryJson().Replace("\"host\":\"127.0.0.1\"", $"\"host\":\"{host}\"");

    [Theory]
    [InlineData("evil.example.com")]
    [InlineData("192.168.1.10")]
    [InlineData("10.0.0.5")]
    [InlineData("8.8.8.8")]
    public void A_record_pointing_outside_this_machine_is_refused_so_the_token_is_never_sent_there(string host)
    {
        var read = BackendDiscovery.Parse(WithHost(host));

        Assert.Equal(DiscoveryKind.Invalid, read.Kind);
        Assert.Contains("hôte non local", read.Detail);
    }

    [Theory]
    [InlineData("127.0.0.1", "127.0.0.1")]
    [InlineData("localhost", "127.0.0.1")]
    [InlineData("0.0.0.0", "127.0.0.1")]
    [InlineData("::", "127.0.0.1")]
    [InlineData("::1", "127.0.0.1")]
    [InlineData("127.0.0.2", "127.0.0.2")]
    public void Local_listening_addresses_are_accepted_and_reached_by_loopback(string host, string reached)
    {
        var read = BackendDiscovery.Parse(WithHost(host));

        Assert.Equal(DiscoveryKind.Found, read.Kind);
        Assert.Equal(reached, BackendDiscovery.ReachableHost(read.Record!.Host));
    }
}
