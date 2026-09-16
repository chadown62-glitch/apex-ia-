using ApexSetup.Dashboard.Services;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class WslStatusParserTests
{
    [Fact]
    public void Parses_the_default_distro_and_running_state()
    {
        const string raw = "  NAME      STATE           VERSION\r\n* Ubuntu    Running         2\r\n  Debian    Stopped         2\r\n";

        var distros = WslStatusParser.Parse(raw);

        Assert.Equal(2, distros.Count);
        Assert.Equal("Ubuntu", distros[0].Name);
        Assert.True(distros[0].IsDefault);
        Assert.True(distros[0].IsRunning);
        Assert.Equal("Debian", distros[1].Name);
        Assert.False(distros[1].IsDefault);
        Assert.False(distros[1].IsRunning);
    }

    [Fact]
    public void Strips_the_null_bytes_wsl_exe_historically_emits_when_stdout_is_redirected()
    {
        // Chaque caractère de la ligne réelle, séparé par un octet nul —
        // reproduit fidèlement la corruption UTF-16LE observée en pratique.
        var corrupted = string.Join("", "* Ubuntu    Running         2".Select(c => c + "\0"));

        var distros = WslStatusParser.Parse("N\0A\0M\0E\0\r\n" + corrupted + "\r\n");

        Assert.Single(distros);
        Assert.Equal("Ubuntu", distros[0].Name);
        Assert.True(distros[0].IsRunning);
    }

    [Fact]
    public void Returns_an_empty_list_for_no_distributions()
    {
        var distros = WslStatusParser.Parse("  NAME      STATE           VERSION\r\n");
        Assert.Empty(distros);
    }

    [Fact]
    public void Ignores_unexpected_malformed_lines_rather_than_guessing()
    {
        var distros = WslStatusParser.Parse("  NAME      STATE           VERSION\r\nsomething-unexpected\r\n* Ubuntu Running 2\r\n");
        Assert.Single(distros);
        Assert.Equal("Ubuntu", distros[0].Name);
    }
}
