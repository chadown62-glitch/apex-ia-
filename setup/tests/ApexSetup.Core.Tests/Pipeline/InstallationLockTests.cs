using ApexSetup.Core.Pipeline;
using Xunit;

namespace ApexSetup.Core.Tests.Pipeline;

public class InstallationLockTests : IDisposable
{
    private readonly string _tempDir = Directory.CreateTempSubdirectory("apex-setup-lock-").FullName;

    public void Dispose() => Directory.Delete(_tempDir, recursive: true);

    [Fact]
    public void TryAcquire_succeeds_when_nothing_else_holds_the_lock()
    {
        using var installLock = InstallationLock.TryAcquire(_tempDir);
        Assert.NotNull(installLock);
    }

    [Fact]
    public void TryAcquire_returns_null_when_another_holder_already_has_the_lock()
    {
        using var first = InstallationLock.TryAcquire(_tempDir);
        Assert.NotNull(first);

        var second = InstallationLock.TryAcquire(_tempDir);

        Assert.Null(second);
    }

    [Fact]
    public void TryAcquire_succeeds_again_after_the_previous_holder_disposes()
    {
        var first = InstallationLock.TryAcquire(_tempDir);
        Assert.NotNull(first);
        first!.Dispose();

        using var second = InstallationLock.TryAcquire(_tempDir);

        Assert.NotNull(second);
    }
}
