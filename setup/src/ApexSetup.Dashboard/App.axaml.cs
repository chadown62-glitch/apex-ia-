using ApexSetup.Dashboard.ViewModels;
using ApexSetup.Dashboard.Views;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;

namespace ApexSetup.Dashboard;

// La classe hérite de `Avalonia.Application`, entièrement qualifié — même patron qu'ApexSetup.Gui.App
// (l'espace de noms `ApexSetup.Application` masquerait le nom simple `Application`).
public sealed class App : Avalonia.Application
{
    private readonly CancellationTokenSource _closing = new();

    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var composition = PanelComposition.Create();
            var log = composition.Log;

            // Un plantage du Panel est journalisé et annoncé par Program (StartupSafety), avant même la fenêtre.
            log.Log($"Panel {Services.PanelInfo.Version} ouvert ({Services.PanelInfo.OperatingSystem}).");
            TaskScheduler.UnobservedTaskException += (_, e) =>
            {
                log.Log($"Erreur asynchrone non observée : {e.Exception.GetBaseException().Message}");
                e.SetObserved();
            };

            var viewModel = new MainWindowViewModel(
                composition.Settings, composition.Status, log, composition.Redactor, composition.DiagnosticContext,
                action => Dispatcher.UIThread.Post(action), composition.Control, composition.Preparer,
                // 0.4.1 : l'assistant s'affiche DANS la fenêtre (WebView2) sous Windows ; APEX_PANEL_BROWSER=1 garde
                // l'ancien comportement (navigateur du système).
                embeddedAssistant: OperatingSystem.IsWindows() && Environment.GetEnvironmentVariable("APEX_PANEL_BROWSER") != "1");
            // 0.4.3 : si le lancement précédent s'est arrêté ou bloqué pendant la création du composant d'affichage
            // (WebView2), celui-ci n'y touche pas : l'assistant passe au navigateur, « Réessayer dans la fenêtre » reste proposé.
            var displayGuard = Services.EmbeddedDisplayGuard.Default();
            if (viewModel.IsEmbeddedAssistant && displayGuard.Interrupted() is { } interrupted)
            {
                displayGuard.End();
                viewModel.ReportInterruptedAttempt(interrupted);
            }

            var window = new MainWindow { DataContext = viewModel, DisplayGuard = displayGuard };
            viewModel.Clipboard = new WindowClipboard(window);
            desktop.MainWindow = window;
            window.Opened += (_, _) => log.Log("Fenêtre du Panel affichée.");

            if (Program.Guard is { } guard)
            {
                // L'accusé part du FIL D'INTERFACE : il prouve au second lancement que cette fenêtre répond.
                guard.OnActivationRequested(() => Dispatcher.UIThread.Post(() =>
                {
                    if (!window.IsVisible)
                    {
                        window.Show(); // fenêtre vivante mais masquée : elle redevient visible
                    }
                    window.WindowState = Avalonia.Controls.WindowState.Normal;
                    window.Activate();
                    guard.Acknowledge();
                }));
            }
            desktop.ShutdownRequested += (_, _) =>
            {
                _closing.Cancel();
                displayGuard.End(); // fermeture normale : jamais prise pour un plantage au prochain lancement
                log.Log("Panel fermé.");
            };

            // Mesure initiale puis rafraîchissement automatique, hors du thread de l'interface.
            _ = Task.Run(async () =>
            {
                await viewModel.InitializeAsync().ConfigureAwait(false);
                try
                {
                    await viewModel.RunAutoRefreshAsync(_closing.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                }
            });
        }

        base.OnFrameworkInitializationCompleted();
    }
}
