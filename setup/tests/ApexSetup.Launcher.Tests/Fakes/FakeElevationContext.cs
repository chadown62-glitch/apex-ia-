using ApexSetup.Launcher.Elevation;

namespace ApexSetup.Launcher.Tests.Fakes;

public sealed class FakeElevationContext : IElevationContext
{
    public bool IsElevated { get; init; }
    public bool RequiresElevationResult { get; init; }
    public string? LastTargetPathQueried { get; private set; }

    public bool RequiresElevationFor(string targetPath)
    {
        LastTargetPathQueried = targetPath;
        return !IsElevated && RequiresElevationResult;
    }
}
