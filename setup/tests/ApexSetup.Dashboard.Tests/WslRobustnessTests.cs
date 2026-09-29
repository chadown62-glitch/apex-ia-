using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Cycle 2 (adversarial) — configurations Windows réelles jamais testées. MOCK TEST : <c>wsl.exe</c>
/// est un double ; les sorties localisées ci-dessous sont ILLUSTRATIVES (le libellé exact de chaque
/// langue n'est pas vérifié ici) : le Panel ne doit dépendre d'AUCUN mot localisé.
/// </summary>
public class WslLocalizationTests
{
    // En-tête et colonne ÉTAT traduits ; un état traduit peut contenir des espaces.
    private const string FrenchLikeList = "  NOM            ÉTAT                    VERSION\n* Ubuntu         En cours d'exécution    2\n  Debian         Arrêté                  2\n";
    private const string GermanLikeList = "  NAME           STATUS                  VERSION\n* Ubuntu         Wird ausgeführt         2\n  Debian         Beendet                 2\n";

    private static FakeProcessRunner Runner(string list, string running) => new FakeProcessRunner()
        .WithRunResponse("--running", FakeProcessRunner.Ok(running))
        .WithRunResponse("-l -v", FakeProcessRunner.Ok(list));

    [Theory]
    [InlineData(FrenchLikeList)]
    [InlineData(GermanLikeList)]
    public async Task A_running_default_distribution_is_READY_whatever_the_Windows_language(string list)
    {
        var probe = await new WslBackendHost(Runner(list, "Ubuntu\n")).ProbeAsync(null);

        Assert.Equal((ComponentStatus.Ready, true, "Ubuntu"), (probe.Status, probe.Running, probe.Distro));
    }

    [Theory]
    [InlineData(FrenchLikeList)]
    [InlineData(GermanLikeList)]
    public async Task A_stopped_distribution_is_OFFLINE_whatever_the_Windows_language(string list)
    {
        var probe = await new WslBackendHost(Runner(list, "Ubuntu\n")).ProbeAsync("Debian");

        Assert.Equal((ComponentStatus.Offline, false), (probe.Status, probe.Running));
    }

    [Fact]
    public async Task The_localized_header_is_never_taken_for_a_distribution()
    {
        var probe = await new WslBackendHost(Runner(FrenchLikeList, "")).ProbeAsync("NOM");

        Assert.Contains("introuvable", probe.Detail);
    }

    [Fact]
    public async Task A_localized_message_when_nothing_runs_is_not_a_distribution_name()
    {
        var probe = await new WslBackendHost(Runner(FrenchLikeList, "Aucune distribution en cours d'exécution.\n")).ProbeAsync(null);

        Assert.Equal((ComponentStatus.Offline, false), (probe.Status, probe.Running));
    }
}

/// <summary>Cycle 2 — launch.sh lancé dans une distribution qui n'est pas celle par défaut.</summary>
public class MultiDistroDiscoveryTests
{
    private const string List = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n  Debian    Running         2\n";

    [Fact]
    public async Task Without_a_chosen_distribution_the_backend_is_found_in_another_running_one()
    {
        var runner = new FakeProcessRunner()
            .WithRunResponse("--running", FakeProcessRunner.Ok("Ubuntu\nDebian\n"))
            .WithRunResponse("-l -v", FakeProcessRunner.Ok(List))
            .WithRunResponse("-d Debian --cd ~ --exec cat -- .apex/runtime/backend.json", FakeProcessRunner.Ok(PanelData.DiscoveryJson(port: 1)))
            .WithRunResponse("-d Ubuntu --cd ~ --exec cat -- .apex/runtime/backend.json", FakeProcessRunner.Exit(1, "cat: x: No such file or directory"))
            .WithRunResponse("--exec cat -- /proc/", FakeProcessRunner.Exit(1, "cat: x: No such file or directory"))
            .WithRunResponse("--exec cat -- " + PanelData.ProjectRoot, FakeProcessRunner.Ok(PanelData.Token));

        var snapshot = await new PanelStatusService(new WslBackendHost(runner), new StatusClient(new HttpClient()))
            .MeasureAsync(new DashboardSettings());

        Assert.Equal("Debian", snapshot.WslDistro);
        Assert.Equal(1, snapshot.Backend.Port); // l'enregistrement de Debian a été trouvé et utilisé
    }

    [Fact]
    public async Task A_chosen_distribution_is_never_overridden()
    {
        var runner = new FakeProcessRunner()
            .WithRunResponse("--running", FakeProcessRunner.Ok("Ubuntu\nDebian\n"))
            .WithRunResponse("-l -v", FakeProcessRunner.Ok(List))
            .WithRunResponse("-d Debian --cd ~ --exec cat", FakeProcessRunner.Ok(PanelData.DiscoveryJson(port: 1)))
            .WithRunResponse("--cd ~ --exec cat", FakeProcessRunner.Exit(1, "cat: x: No such file or directory"));

        var snapshot = await new PanelStatusService(new WslBackendHost(runner), new StatusClient(new HttpClient()))
            .MeasureAsync(new DashboardSettings { WslDistro = "Ubuntu", ApiPort = 1 });

        Assert.Equal("Ubuntu", snapshot.WslDistro);
        Assert.Equal(NotReadyCause.BackendNotStarted, snapshot.Cause);
    }
}
