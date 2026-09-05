using ApexSetup.Core.Preflight;

namespace ApexSetup.Core.Tests.Fixtures;

internal sealed class FakeEnvironmentProbe(long availableDiskBytes, bool canWrite) : IEnvironmentProbe
{
    public long GetAvailableDiskBytes(string path) => availableDiskBytes;
    public bool CanWriteTo(string directoryPath) => canWrite;
}
