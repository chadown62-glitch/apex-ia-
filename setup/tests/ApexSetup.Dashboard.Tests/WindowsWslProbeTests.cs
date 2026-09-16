using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class WindowsWslProbeTests
{
    private const string ListOutput = "  NAME      STATE           VERSION\r\n* Ubuntu    Running         2\r\n  Debian    Stopped         2\r\n";

    [Fact]
    public async Task Reports_NotInstalled_when_wsl_exe_cannot_be_started()
    {
        var runner = new FakeProcessRunner().WithRunResponse("-l", ProcessRunResult.NotStarted("introuvable"));
        var probe = new WindowsWslProbe(runner);

        var state = await probe.ProbeAsync(null);

        Assert.Equal(ComponentStatus.NotInstalled, state.Status);
    }

    [Fact]
    public async Task Reports_Running_for_the_default_distribution_when_none_is_preferred()
    {
        var runner = new FakeProcessRunner().WithRunResponse("-l", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = ListOutput });
        var probe = new WindowsWslProbe(runner);

        var state = await probe.ProbeAsync(null);

        Assert.Equal(ComponentStatus.Running, state.Status);
        Assert.Contains("Ubuntu", state.Detail);
    }

    [Fact]
    public async Task Reports_Stopped_for_a_preferred_distro_that_is_installed_but_not_running()
    {
        var runner = new FakeProcessRunner().WithRunResponse("-l", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = ListOutput });
        var probe = new WindowsWslProbe(runner);

        var state = await probe.ProbeAsync("Debian");

        Assert.Equal(ComponentStatus.Stopped, state.Status);
    }

    [Fact]
    public async Task Reports_Error_for_a_preferred_distro_that_does_not_exist()
    {
        var runner = new FakeProcessRunner().WithRunResponse("-l", new ProcessRunResult { Started = true, ExitCode = 0, Stdout = ListOutput });
        var probe = new WindowsWslProbe(runner);

        var state = await probe.ProbeAsync("Alpine");

        Assert.Equal(ComponentStatus.Error, state.Status);
    }

    [Fact]
    public async Task Reports_Unknown_when_wsl_exe_times_out()
    {
        var runner = new FakeProcessRunner().WithRunResponse("-l", new ProcessRunResult { Started = true, TimedOut = true });
        var probe = new WindowsWslProbe(runner);

        var state = await probe.ProbeAsync(null);

        Assert.Equal(ComponentStatus.Unknown, state.Status);
    }

    [Fact]
    public async Task PathExistsAsync_reflects_the_real_exit_code_of_the_test_command()
    {
        var runner = new FakeProcessRunner().WithRunResponse("test -d", new ProcessRunResult { Started = true, ExitCode = 0 });
        var probe = new WindowsWslProbe(runner);

        Assert.True(await probe.PathExistsAsync(null, "/home/user/apex-ia-"));
    }

    [Fact]
    public async Task PathExistsAsync_is_false_for_a_nonzero_exit_code()
    {
        var runner = new FakeProcessRunner().WithRunResponse("test -d", new ProcessRunResult { Started = true, ExitCode = 1 });
        var probe = new WindowsWslProbe(runner);

        Assert.False(await probe.PathExistsAsync(null, "/does/not/exist"));
    }
}
