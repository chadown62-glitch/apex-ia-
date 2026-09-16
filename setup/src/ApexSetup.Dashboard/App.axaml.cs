using ApexSetup.Application;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.ViewModels;
using ApexSetup.Dashboard.Views;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;

namespace ApexSetup.Dashboard;

// La classe hérite de `Avalonia.Application`, entièrement qualifié —
// même patron qu'ApexSetup.Gui.App : `namespace ApexSetup.Dashboard`
// imbriqué sous `ApexSetup` rend `ApexSetup.Application` (le PROJET,
// une autre branche du même arbre de namespaces) prioritaire sur `using
// Avalonia;` pour le simple nom `Application` — jamais une ambiguïté
// accidentelle, une contrainte du langage déjà rencontrée par Gui.
public sealed class App : Avalonia.Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            // Racine de composition — assemble UNIQUEMENT des implémentations
            // réelles, jamais un double de test (même discipline que
            // ApexSetup.Gui.App). `SetupApplicationServiceFactory.Create()`
            // est la MÊME fabrique déjà utilisée par ApexSetup.Cli et
            // ApexSetup.Gui — jamais une seconde construction divergente
            // d'ISetupApplicationService.
            var runtimeService = SetupApplicationServiceFactory.Create();
            var processRunner = new RealProcessRunner();
            var wslProbe = new WindowsWslProbe(processRunner);
            var runtimeStatusService = new RuntimeStatusService(runtimeService);
            var httpClient = new HttpClient();
            var apiHealthChecker = new ApiHealthChecker(processRunner, httpClient);
            var webPanelChecker = new WebPanelChecker(httpClient);
            var browserLauncher = new RealBrowserLauncher();
            var logSink = new InMemoryLogSink();
            var orchestrator = new ApexOrchestrator(
                processRunner, wslProbe, runtimeStatusService, apiHealthChecker, webPanelChecker, browserLauncher, logSink);
            var settingsStore = new JsonSettingsStore(JsonSettingsStore.DefaultFilePath);

            var mainViewModel = new MainWindowViewModel(
                settingsStore, wslProbe, orchestrator, runtimeStatusService, apiHealthChecker, webPanelChecker, logSink);

            var window = new MainWindow { DataContext = mainViewModel };
            desktop.MainWindow = window;

            // Chargement initial asynchrone — jamais bloquant sur le thread
            // UI au démarrage (mission §5 : aucun délai fixe, uniquement des
            // vérifications réelles, qui prennent un temps variable).
            _ = mainViewModel.InitializeAsync();
        }

        base.OnFrameworkInitializationCompleted();
    }
}
