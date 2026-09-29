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

            // Un plantage du Panel laisse une trace exploitable dans le journal fichier.
            AppDomain.CurrentDomain.UnhandledException += (_, e) =>
                log.Log($"PLANTAGE du Panel : {(e.ExceptionObject as Exception)?.ToString() ?? e.ExceptionObject}");
            TaskScheduler.UnobservedTaskException += (_, e) =>
            {
                log.Log($"Erreur asynchrone non observée : {e.Exception.GetBaseException().Message}");
                e.SetObserved();
            };

            var viewModel = new MainWindowViewModel(
                composition.Settings, composition.Status, log, composition.Redactor, composition.DiagnosticContext,
                action => Dispatcher.UIThread.Post(action));
            var window = new MainWindow { DataContext = viewModel };
            viewModel.Clipboard = new WindowClipboard(window);
            desktop.MainWindow = window;

            if (Program.Guard is { } guard)
            {
                guard.ActivationRequested += () => Dispatcher.UIThread.Post(() =>
                {
                    window.WindowState = Avalonia.Controls.WindowState.Normal;
                    window.Activate();
                });
            }
            desktop.ShutdownRequested += (_, _) =>
            {
                _closing.Cancel();
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
