using ApexSetup.Application.Tests.Fixtures;
using Xunit;

namespace ApexSetup.Application.Tests;

public class VersionCommandTests
{
    [Fact]
    public async Task GetVersionAsync_reports_both_setup_and_runtime_versions_when_runtime_is_reachable()
    {
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        var response = await service.GetVersionAsync();

        Assert.True(response.Success);
        Assert.Equal("test-1.0.0", response.SetupVersion);
        Assert.NotNull(response.Runtime);
        Assert.Equal("0.1.2", response.Runtime!.Version);
        Assert.Empty(response.Warnings);
    }

    [Fact]
    public async Task GetVersionAsync_still_succeeds_with_a_warning_when_the_runtime_is_unreachable()
    {
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("version");
        var service = ServiceFactory.Build(runner);

        var response = await service.GetVersionAsync();

        Assert.True(response.Success); // setup version command itself never fails on a missing Runtime
        Assert.Null(response.Runtime);
        Assert.Single(response.Warnings);
        Assert.Equal("runtime_unreachable", response.Warnings[0].Code);
    }
}
