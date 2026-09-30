using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

/// <summary>
/// Sorties RÉELLES de <c>wsl.exe</c>, relevées sur un Windows réel (GitHub Actions, Windows Server 2025
/// 10.0.26100, WSL 2.7.14.0, aucune distribution installée — exécution 36701035152 du workflow
/// <c>windows-desktop</c>), rejouées ici par un double de processus : le TEXTE est réel, l'exécution
/// dans ce test ne l'est pas (MOCK TEST alimenté par une observation réelle).
/// </summary>
public class RealWslOutputTests
{
    /// <summary><c>wsl.exe -l -v</c> sans distribution : code de sortie -1 et ce message (5 lignes).</summary>
    private const string NoDistributionMessage =
        "Windows Subsystem for Linux has no installed distributions.\r\n"
        + "You can resolve this by installing a distribution with the instructions below:\r\n"
        + "\r\n"
        + "Use 'wsl.exe --list --online' to list available distributions\r\n"
        + "and 'wsl.exe --install <Distro>' to install.\r\n";

    /// <summary>
    /// Ce que <see cref="RealProcessRunner"/> reçoit sans <c>WSL_UTF8</c> : de l'UTF-16LE (octets
    /// 57 00 69 00 …) décodé en UTF-8, soit un caractère nul après chaque caractère ASCII.
    /// </summary>
    private static string AsUtf16DecodedAsUtf8(string text) => string.Concat(text.Select(c => c + "\0"));

    public static TheoryData<string> NoDistributionOutputs => new()
    {
        AsUtf16DecodedAsUtf8(NoDistributionMessage), // sans WSL_UTF8 (octets observés : 504)
        NoDistributionMessage,                       // avec WSL_UTF8=1 (octets observés : 252)
    };

    [Theory]
    [MemberData(nameof(NoDistributionOutputs))]
    public async Task No_installed_distribution_is_never_read_as_a_stopped_distribution_named_from_the_message(string stdout)
    {
        var runner = new FakeProcessRunner()
            .WithRunResponse("--running", FakeProcessRunner.Ok("")) // observé : code 0, sortie vide
            .WithRunResponse("-l -v", new ProcessRunResult { Started = true, ExitCode = -1, Stdout = stdout });

        var probe = await new WslBackendHost(runner).ProbeAsync(null);

        Assert.Equal((ComponentStatus.Error, false, NotReadyCause.WslNotInstalled), (probe.Status, probe.Running, probe.Cause));
        Assert.Contains("aucune distribution", probe.Detail);
        Assert.DoesNotContain(new[] { "You", "Use", "and" }, word => probe.Distro == word || probe.Detail.StartsWith(word + " "));
    }

    [Fact]
    public async Task A_failing_list_is_never_parsed_as_distributions_whatever_its_text()
    {
        var runner = new FakeProcessRunner()
            .WithRunResponse("--running", FakeProcessRunner.Ok(""))
            .WithRunResponse("-l -v", new ProcessRunResult
            {
                Started = true,
                ExitCode = 1,
                Stdout = "  NAME      STATE           VERSION\n* Ubuntu    Running         2\n", // texte trompeur
            });

        var probe = await new WslBackendHost(runner).ProbeAsync(null);

        Assert.False(probe.Running);
        Assert.NotEqual(ComponentStatus.Ready, probe.Status);
    }

    private sealed class FakeRegistry(IReadOnlyList<WslRegisteredDistro>? distros) : IWslRegistry
    {
        public IReadOnlyList<WslRegisteredDistro>? ReadDistributions() => distros;
    }

    private static FakeProcessRunner Runner(int listExit, string list, string running) => new FakeProcessRunner()
        .WithRunResponse("--running", FakeProcessRunner.Ok(running))
        .WithRunResponse("-l -v", new ProcessRunResult { Started = true, ExitCode = listExit, Stdout = list });

    [Fact]
    public async Task Registry_gives_names_default_and_WSL_version_without_reading_any_translated_text()
    {
        var registry = new FakeRegistry([new("Debian", 1, false), new("Ubuntu-24.04", 2, true)]);
        // Colonne ÉTAT volontairement fausse : seul l'ensemble des noms en cours compte.
        var probe = await new WslBackendHost(Runner(0, "  NOM ÉTAT VERSION\n* Ubuntu-24.04 Arrêté 2\n", "Ubuntu-24.04\n"), registry)
            .ProbeAsync(null);

        Assert.Equal((ComponentStatus.Ready, true, "Ubuntu-24.04"), (probe.Status, probe.Running, probe.Distro));
        Assert.Contains("WSL 2", probe.Detail);
    }

    [Fact]
    public async Task An_empty_registry_means_no_distribution_even_if_the_list_text_looks_like_one()
    {
        var probe = await new WslBackendHost(Runner(0, "  NAME STATE VERSION\n* Ubuntu Running 2\n", "Ubuntu\n"), new FakeRegistry([]))
            .ProbeAsync(null);

        Assert.Equal((ComponentStatus.Error, NotReadyCause.WslNotInstalled), (probe.Status, probe.Cause));
    }

    [Fact]
    public async Task An_unreadable_registry_falls_back_to_a_successful_list_only()
    {
        var ok = await new WslBackendHost(Runner(0, "  NAME STATE VERSION\n* Ubuntu Running 2\n", "Ubuntu\n"), new FakeRegistry(null))
            .ProbeAsync(null);
        var failed = await new WslBackendHost(Runner(-1, NoDistributionMessage, ""), new FakeRegistry(null)).ProbeAsync(null);

        Assert.Equal((ComponentStatus.Ready, "Ubuntu"), (ok.Status, ok.Distro));
        Assert.Equal(NotReadyCause.WslNotInstalled, failed.Cause);
    }

    [Fact]
    public async Task Registered_distributions_with_a_failing_list_are_reported_as_WSL_not_responding()
    {
        var probe = await new WslBackendHost(Runner(-1, "Error code: Wsl/Service/E_UNEXPECTED\r\n", ""), new FakeRegistry([new("Ubuntu", 2, true)]))
            .ProbeAsync(null);

        Assert.Equal((ComponentStatus.Error, false, NotReadyCause.WslUnresponsive), (probe.Status, probe.Running, probe.Cause));
        Assert.Contains("E_UNEXPECTED", probe.Detail);
    }
}
