using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.Tests.Fixtures;
using ApexSetup.Dashboard.ViewModels;
using Xunit;

namespace ApexSetup.Dashboard.Tests;

public class SettingsViewModelTests
{
    [Fact]
    public async Task VerifyPathCommand_reports_success_only_after_a_real_empirical_check()
    {
        var wslProbe = new FakeWslProbe { PathExists = true };
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), wslProbe, new DashboardSettings())
        {
            ProjectPathWsl = "/home/user/apex-ia-",
        };
        Assert.Null(vm.PathVerified);

        await vm.VerifyPathCommand.ExecuteAsync();

        Assert.True(vm.PathVerified);
    }

    [Fact]
    public async Task VerifyPathCommand_reports_failure_honestly_for_a_path_that_does_not_exist()
    {
        var wslProbe = new FakeWslProbe { PathExists = false };
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), wslProbe, new DashboardSettings())
        {
            ProjectPathWsl = "/does/not/exist",
        };

        await vm.VerifyPathCommand.ExecuteAsync();

        Assert.False(vm.PathVerified);
    }

    [Fact]
    public async Task Editing_the_path_after_verification_resets_the_verified_flag_never_trusting_a_stale_check()
    {
        var wslProbe = new FakeWslProbe { PathExists = true };
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), wslProbe, new DashboardSettings()) { ProjectPathWsl = "/a" };
        await vm.VerifyPathCommand.ExecuteAsync();
        Assert.True(vm.PathVerified);

        vm.ProjectPathWsl = "/b";

        Assert.Null(vm.PathVerified);
    }

    [Fact]
    public async Task SaveCommand_persists_and_raises_Saved_with_the_exact_settings_entered()
    {
        var path = TempFile();
        var store = new JsonSettingsStore(path);
        var vm = new SettingsViewModel(store, new FakeWslProbe(), new DashboardSettings())
        {
            ProjectPathWsl = "/home/user/apex-ia-", ApiPort = 8123,
        };
        DashboardSettings? saved = null;
        vm.Saved += (_, s) => saved = s;

        await vm.SaveCommand.ExecuteAsync();

        Assert.NotNull(saved);
        Assert.Equal("/home/user/apex-ia-", saved!.ProjectPathWsl);
        Assert.Equal(8123, saved.ApiPort);
        var reloaded = await store.LoadAsync();
        Assert.Equal(saved, reloaded);
    }

    [Fact]
    public async Task SaveCommand_shows_a_readable_message_instead_of_crashing_when_persistence_fails()
    {
        // Régression (RELEASE LOCK 2/2, corrigé lors de la finalisation 1/2, non couvert par
        // un test jusqu'ici) : un échec réel de SaveAsync (ex. %AppData%\Apex refusé, disque
        // plein) ne doit ni fermer APEX.exe ni rester invisible — VerificationMessage doit
        // porter un message lisible, réutilisant l'affichage déjà présent en Paramètres.
        var vm = new SettingsViewModel(new ThrowingSettingsStore(), new FakeWslProbe(), new DashboardSettings())
        {
            ProjectPathWsl = "/home/user/apex-ia-",
        };

        var exception = await Record.ExceptionAsync(() => vm.SaveCommand.ExecuteAsync());

        Assert.Null(exception);
        Assert.NotNull(vm.VerificationMessage);
        Assert.Contains("Enregistrement impossible", vm.VerificationMessage);
    }

    [Fact]
    public void CloseCommand_raises_CloseRequested()
    {
        var vm = new SettingsViewModel(new JsonSettingsStore(TempFile()), new FakeWslProbe(), new DashboardSettings());
        var raised = false;
        vm.CloseRequested += (_, _) => raised = true;

        vm.CloseCommand.Execute(null);

        Assert.True(raised);
    }

    private static string TempFile() => Path.Combine(Path.GetTempPath(), "apex-settings-test-" + Guid.NewGuid() + ".json");

    /// <summary>Simule un échec réel de persistance (ex. dossier refusé, disque plein) — jamais rencontré par les autres tests, qui utilisent un vrai fichier temporaire.</summary>
    private sealed class ThrowingSettingsStore : ISettingsStore
    {
        public Task<DashboardSettings> LoadAsync(CancellationToken cancellationToken = default) =>
            Task.FromResult(new DashboardSettings());

        public Task SaveAsync(DashboardSettings settings, CancellationToken cancellationToken = default) =>
            throw new IOException("disque plein (simulé)");
    }
}
