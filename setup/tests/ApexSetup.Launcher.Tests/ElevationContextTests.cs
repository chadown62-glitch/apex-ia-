using ApexSetup.Launcher.Elevation;
using ApexSetup.Launcher.Tests.Fakes;
using Xunit;

namespace ApexSetup.Launcher.Tests;

public class ElevationContextTests
{
    [Fact]
    public void Null_context_used_on_non_windows_never_requires_elevation()
    {
        // Repli non-Windows — jamais utilisé en production (packagée
        // win-x64 uniquement) mais nécessaire à la compilation/exécution
        // sur les plateformes de CI de ce dépôt (voir sa docstring).
        var context = new NullElevationContext();
        Assert.True(context.IsElevated);
        Assert.False(context.RequiresElevationFor("/anywhere"));
    }

    [Fact]
    public void Already_elevated_context_never_requests_elevation_again_no_uac_loop()
    {
        var fake = new FakeElevationContext { IsElevated = true, RequiresElevationResult = true };
        Assert.False(fake.RequiresElevationFor("/some/protected/path"));
    }

    [Fact]
    public void Not_elevated_context_reports_the_configured_requirement()
    {
        var fake = new FakeElevationContext { IsElevated = false, RequiresElevationResult = true };
        Assert.True(fake.RequiresElevationFor("/some/protected/path"));
        Assert.Equal("/some/protected/path", fake.LastTargetPathQueried);
    }
}
