using ApexSetup.Application.Requests;
using ApexSetup.Application.Tests.Fixtures;
using ApexSetup.Core.Contracts;
using Xunit;

namespace ApexSetup.Application.Tests;

public class InstallCommandTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-setup-install-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-setup-install-dest-").FullName;
    private readonly string _workingDirectory = Directory.CreateTempSubdirectory("apex-setup-install-work-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        if (Directory.Exists(_destination)) Directory.Delete(_destination, recursive: true);
        Directory.Delete(_workingDirectory, recursive: true);
    }

    private SetupInstallRequest MakeRequest(string manifestPath) => new()
    {
        Plan = new SetupPlanRequest
        {
            TargetApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifestPath, ProfileOverride = HardwareProfileTier.Minimal,
        },
        WorkingDirectory = _workingDirectory,
    };

    [Fact]
    public async Task InstallAsync_never_touches_the_destination_when_the_gate_blocks_phase_2()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new RoutingFakeRuntimeProcessRunner()
            .WithResponse("version", SamplePayloads.Version)
            .WithResponse("doctor", SamplePayloads.DoctorPass)
            .WithResponse("gate", SamplePayloads.GateFailBlocking);
        var service = ServiceFactory.Build(runner);

        var response = await service.InstallAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.False(response.Committed);
        Assert.Empty(response.ExecutedSteps);
        Assert.Equal("gate_blocking", response.Errors[0].Code);
        // AUCUN artefact ni marqueur de commit — jamais un octet écrit avant que Gate n'autorise.
        Assert.False(Directory.Exists(Path.Combine(_destination, "artifacts")));
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
    }

    [Fact]
    public async Task InstallAsync_never_touches_the_destination_when_the_runtime_is_unreachable()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var runner = new RoutingFakeRuntimeProcessRunner().WithNotFound("version").WithNotFound("doctor").WithNotFound("gate");
        var service = ServiceFactory.Build(runner);

        var response = await service.InstallAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.Equal("runtime_unavailable", response.Errors[0].Code);
        Assert.Empty(response.ExecutedSteps);
    }

    [Fact]
    public async Task InstallAsync_completes_and_commits_a_real_end_to_end_installation()
    {
        var fastContent = "contenu du modele fast pour le test"u8.ToArray();
        var embedContent = "contenu du modele embed pour le test"u8.ToArray();
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            var path = ctx.Request.Url!.AbsolutePath.TrimStart('/');
            var body = path switch { "fast-model" => fastContent, "embed-model" => embedContent, _ => null };
            if (body is null) { ctx.Response.StatusCode = 404; ctx.Response.Close(); return; }
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });
        var manifest = ManifestFiles.WriteDownloadableManifest(_manifestDir, server.BaseUrl, fastContent, embedContent);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner(), httpClient: new HttpClient());

        var response = await service.InstallAsync(MakeRequest(manifest));

        Assert.True(response.Success);
        Assert.True(response.Committed);
        Assert.Equal(["download", "integrity_verification", "installation", "configuration", "validation", "commit"], response.ExecutedSteps);
        Assert.True(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
        Assert.Equal(fastContent, await File.ReadAllBytesAsync(Path.Combine(_destination, "artifacts", "qwen2-5-coder-7b-instruct")));
        Assert.Equal(embedContent, await File.ReadAllBytesAsync(Path.Combine(_destination, "artifacts", "bge-m3-latest")));
        // Jamais de fichier de verrou résiduel après un commit réussi (voir InstallationLock.Dispose).
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup.lock")));
        // L'état transactionnel N'EST PAS supprimé après un succès (comportement Phase 2 volontaire :
        // il reste sur disque avec Phase=Committed, cohérent avec le marqueur de commit — voir
        // RecoveryClassifier, qui classe ceci comme Completed, jamais Corrupt). Seul un ÉCHEC
        // suivi d'un rollback complet efface l'état (voir le test de rollback ci-dessous).
        var persisted = ApexSetup.Core.Pipeline.InstallationStateStore.TryRead(_destination);
        Assert.NotNull(persisted);
        Assert.Equal(ApexSetup.Core.Pipeline.InstallationLifecyclePhase.Committed, persisted!.Phase);
        Assert.Equal(
            ApexSetup.Core.Pipeline.RecoveryState.Completed,
            ApexSetup.Core.Pipeline.RecoveryClassifier.Classify(_destination).State);
    }

    [Fact]
    public async Task InstallAsync_rolls_back_every_completed_stage_when_a_later_stage_fails_and_never_declares_success()
    {
        var fastContent = "contenu du modele fast pour le test"u8.ToArray();
        var embedContent = "contenu du modele embed pour le test"u8.ToArray();
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            var path = ctx.Request.Url!.AbsolutePath.TrimStart('/');
            var body = path switch { "fast-model" => fastContent, "embed-model" => embedContent, _ => null };
            if (body is null) { ctx.Response.StatusCode = 404; ctx.Response.Close(); return; }
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });
        // Taille déclarée MENSONGÈRE pour "fast" — jamais vérifiée avant Validation,
        // qui échouera donc APRÈS Download/Integrity/Installation/Configuration
        // aient tous réussi : un vrai rollback multi-étapes, pas un simple échec précoce.
        var manifest = ManifestFiles.WriteDownloadableManifest(
            _manifestDir, server.BaseUrl, fastContent, embedContent, fastDeclaredSizeOverride: fastContent.Length + 999);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner(), httpClient: new HttpClient());

        var response = await service.InstallAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.False(response.Committed);
        Assert.Equal("validation", response.FailedStep);
        Assert.Contains("installation", response.RolledBackSteps);
        Assert.Contains("configuration", response.RolledBackSteps);
        Assert.Empty(response.FailedRollbackSteps);
        // Jamais de succès déclaré avant Commit, et Commit n'a jamais tourné.
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
        // Rollback réel : les artefacts installés ont été retirés.
        Assert.False(File.Exists(Path.Combine(_destination, "artifacts", "qwen2-5-coder-7b-instruct")));
        // État transactionnel nettoyé après un rollback complet.
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-state.json")));
    }

    [Fact]
    public async Task InstallAsync_refuses_a_second_concurrent_run_at_the_same_destination_with_a_structured_error()
    {
        var manifest = ManifestFiles.WriteMinimalProfileManifest(_manifestDir);
        var service = ServiceFactory.Build(ServiceFactory.HealthyRunner());

        using var heldLock = ApexSetup.Core.Pipeline.InstallationLock.TryAcquire(_destination);
        Assert.NotNull(heldLock); // précondition : le test tient bien le verrou lui-même

        var response = await service.InstallAsync(MakeRequest(manifest));

        Assert.False(response.Success);
        Assert.Equal("concurrency_refused", response.Errors[0].Code);
        Assert.Empty(response.ExecutedSteps); // jamais une étape entamée pendant qu'une autre exécution tient le verrou
    }
}
