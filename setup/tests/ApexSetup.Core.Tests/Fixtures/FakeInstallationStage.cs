using ApexSetup.Core.Pipeline;

namespace ApexSetup.Core.Tests.Fixtures;

/// <summary>
/// Double de test de <see cref="IInstallationStage"/> — isole la logique
/// d'ORCHESTRATION de <see cref="InstallationPipeline"/> (ordre, rollback
/// inverse, best-effort) des étapes réelles, elles-mêmes testées
/// séparément avec de vrais fichiers/réseau (voir <c>Stages/*Tests.cs</c>,
/// <c>Download/HttpArtifactDownloaderTests.cs</c>).
/// </summary>
public sealed class FakeInstallationStage(string name, bool shouldSucceed = true, bool rollbackThrows = false) : IInstallationStage
{
    public string Name => name;
    public bool ExecuteCalled { get; private set; }
    public bool RollbackCalled { get; private set; }

    public Task<StageOutcome> ExecuteAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        ExecuteCalled = true;
        return Task.FromResult(shouldSucceed ? StageOutcome.Ok() : StageOutcome.Failed($"{name} configuré pour échouer"));
    }

    public Task RollbackAsync(InstallationContext context, CancellationToken cancellationToken = default)
    {
        RollbackCalled = true;
        if (rollbackThrows)
        {
            throw new InvalidOperationException($"rollback de {name} configuré pour lever");
        }
        return Task.CompletedTask;
    }
}
