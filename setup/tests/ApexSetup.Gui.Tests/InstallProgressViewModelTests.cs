using ApexSetup.Application;
using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Gui.Services;
using ApexSetup.Gui.Tests.Fixtures;
using ApexSetup.Gui.ViewModels;
using Xunit;

namespace ApexSetup.Gui.Tests;

public class InstallProgressViewModelTests : IDisposable
{
    private readonly string _manifestDir = Directory.CreateTempSubdirectory("apex-gui-install-manifest-").FullName;
    private readonly string _destination = Directory.CreateTempSubdirectory("apex-gui-install-dest-").FullName;
    private readonly string _workingDirectory = Directory.CreateTempSubdirectory("apex-gui-install-work-").FullName;

    public void Dispose()
    {
        Directory.Delete(_manifestDir, recursive: true);
        if (Directory.Exists(_destination)) Directory.Delete(_destination, recursive: true);
        Directory.Delete(_workingDirectory, recursive: true);
    }

    [Fact]
    public async Task Never_reports_Committed_before_the_real_pipeline_returns_success()
    {
        var fastContent = "gui-fast"u8.ToArray();
        var embedContent = "gui-embed"u8.ToArray();
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
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest, WorkingDirectory = _workingDirectory,
        };
        var vm = new InstallProgressViewModel(service, session, new FakeNavigationService());

        Assert.False(vm.Committed); // avant toute installation : jamais "committed" par défaut

        await vm.InstallCommand.ExecuteAsync();

        Assert.False(vm.IsBusy);
        Assert.True(vm.Committed);
        Assert.True(vm.ContinueCommand.CanExecute(null));
        Assert.True(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
    }

    [Fact]
    public async Task A_real_cancellation_actually_stops_the_pipeline_and_never_reports_success()
    {
        // Réseau JAMAIS servi (aucun handler) -> le téléchargement resterait
        // bloqué indéfiniment sans annulation ; l'annulation réelle doit
        // interrompre l'appel avant qu'il ne se termine de lui-même.
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30));
            ctx.Response.StatusCode = 500;
            ctx.Response.Close();
        });
        var fastContent = "gui-fast"u8.ToArray();
        var embedContent = "gui-embed"u8.ToArray();
        var manifest = ManifestFiles.WriteDownloadableManifest(_manifestDir, server.BaseUrl, fastContent, embedContent);
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest, WorkingDirectory = _workingDirectory,
        };
        var vm = new InstallProgressViewModel(service, session, new FakeNavigationService());

        var installTask = vm.InstallCommand.ExecuteAsync();
        // Laisse l'installation réellement démarrer (verrou pris, requête HTTP en vol) avant d'annuler.
        while (!vm.IsBusy)
        {
            await Task.Delay(5);
        }
        vm.RequestCancel();
        await installTask;

        Assert.False(vm.IsBusy);
        Assert.True(vm.WasCancelled);
        Assert.False(vm.Committed);
        Assert.False(File.Exists(Path.Combine(_destination, ".apex-setup-installed")));
    }

    [Fact]
    public async Task A_second_concurrent_invocation_of_the_same_command_is_a_no_op_never_a_double_install()
    {
        var fastContent = "gui-fast"u8.ToArray();
        var embedContent = "gui-embed"u8.ToArray();
        var callCount = 0;
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            Interlocked.Increment(ref callCount);
            var path = ctx.Request.Url!.AbsolutePath.TrimStart('/');
            var body = path switch { "fast-model" => fastContent, "embed-model" => embedContent, _ => null };
            if (body is null) { ctx.Response.StatusCode = 404; ctx.Response.Close(); return; }
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });
        var manifest = ManifestFiles.WriteDownloadableManifest(_manifestDir, server.BaseUrl, fastContent, embedContent);
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest, WorkingDirectory = _workingDirectory,
        };
        var vm = new InstallProgressViewModel(service, session, new FakeNavigationService());

        var first = vm.InstallCommand.ExecuteAsync();
        var second = vm.InstallCommand.ExecuteAsync(); // double-clic simulé
        await Task.WhenAll(first, second);

        Assert.True(vm.Committed);
        // 2 artefacts (fast + embed) téléchargés une seule fois chacun -> jamais 4 (pas de double exécution réelle du pipeline).
        Assert.Equal(2, callCount);
    }

    // -----------------------------------------------------------------
    // PHASE 10 — revue adversariale : bouton Annuler jamais actionnable
    // -----------------------------------------------------------------
    // AVANT correctif : CancelCommand.CanExecute() (lié à
    // InstallCommand.IsRunning) n'était évalué qu'UNE FOIS au binding XAML
    // (IsRunning=false à ce moment), jamais réévalué ensuite — le bouton
    // restait désactivé pendant toute la durée d'une installation réelle.
    [Fact]
    public async Task CancelCommand_becomes_executable_once_the_install_actually_starts()
    {
        var fastContent = "gui-fast"u8.ToArray();
        var embedContent = "gui-embed"u8.ToArray();
        var release = new TaskCompletionSource();
        await using var server = new LocalHttpTestServer(async ctx =>
        {
            await release.Task; // bloque le téléchargement -> l'installation reste "en cours" jusqu'à ce que ce test la débloque
            var path = ctx.Request.Url!.AbsolutePath.TrimStart('/');
            var body = path switch { "fast-model" => fastContent, "embed-model" => embedContent, _ => null };
            if (body is null) { ctx.Response.StatusCode = 404; ctx.Response.Close(); return; }
            ctx.Response.ContentLength64 = body.Length;
            await ctx.Response.OutputStream.WriteAsync(body);
            ctx.Response.Close();
        });
        var manifest = ManifestFiles.WriteDownloadableManifest(_manifestDir, server.BaseUrl, fastContent, embedContent);
        var service = TestServiceFactory.Build(TestServiceFactory.HealthyRunner());
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = manifest, WorkingDirectory = _workingDirectory,
        };
        var vm = new InstallProgressViewModel(service, session, new FakeNavigationService());

        Assert.False(vm.CancelCommand.CanExecute(null)); // avant tout lancement : rien à annuler

        var installTask = vm.InstallCommand.ExecuteAsync();
        while (!vm.IsBusy)
        {
            await Task.Delay(5);
        }

        Assert.True(vm.CancelCommand.CanExecute(null)); // installation réellement en cours -> le bouton doit être actionnable

        release.SetResult();
        await installTask;

        Assert.False(vm.CancelCommand.CanExecute(null)); // terminée -> plus rien à annuler
    }

    // -----------------------------------------------------------------
    // PHASE 10 — revue adversariale : exception inattendue pendant
    // l'installation faisait planter tout le processus GUI
    // -----------------------------------------------------------------
    // AVANT correctif : seul OperationCanceledException était capturée ;
    // AsyncRelayCommand.Execute est `async void`, donc toute AUTRE
    // exception (ex. levée avant même que le pipeline ne démarre) terminait
    // le processus entier sans rollback confirmé ni erreur affichée.
    [Fact]
    public async Task An_unexpected_exception_is_surfaced_as_an_error_never_escapes_uncaught()
    {
        var service = new ThrowingSetupApplicationService();
        var session = new SetupSession
        {
            ApexVersion = "0.1.2", Destination = _destination, ManifestPath = "irrelevant.json", WorkingDirectory = _workingDirectory,
        };
        var vm = new InstallProgressViewModel(service, session, new FakeNavigationService());

        // Si l'exception n'était pas capturée, ceci lèverait au lieu de
        // revenir normalement — la preuve la plus directe de la régression.
        await vm.InstallCommand.ExecuteAsync();

        Assert.False(vm.IsBusy);
        Assert.False(vm.Committed);
        Assert.NotNull(vm.ErrorMessage);
        Assert.Contains("panne simulée pour ce test", vm.ErrorMessage);
    }

    /// <summary>Double de test minimal — seule <see cref="InstallAsync"/> est jamais invoquée par <see cref="InstallProgressViewModel"/>.</summary>
    private sealed class ThrowingSetupApplicationService : ISetupApplicationService
    {
        public Task<InstallResponse> InstallAsync(SetupInstallRequest request, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("panne simulée pour ce test — jamais capturée avant la régression corrigée en revue adversariale Phase 10");

        public Task<VersionResponse> GetVersionAsync(CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<DetectResponse> DetectAsync(string destinationForDiskProbe, CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<CheckResponse> CheckAsync(SetupPlanRequest request, CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<PlanResponse> PlanAsync(SetupPlanRequest request, CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<RecoverResponse> RecoverAsync(string destination, CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<ValidateResponse> ValidateAsync(CancellationToken cancellationToken = default) => throw new NotImplementedException();
        public Task<StatusResponse> StatusAsync(string destination, CancellationToken cancellationToken = default) => throw new NotImplementedException();
    }
}
