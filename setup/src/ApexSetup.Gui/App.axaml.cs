using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using ApexSetup.Application;
using ApexSetup.Gui.Services;
using ApexSetup.Gui.ViewModels;
using ApexSetup.Gui.Views;

namespace ApexSetup.Gui;

public sealed class App : Avalonia.Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            // PHASE 4 : racine de composition du GUI — construit
            // ISetupApplicationService via la MÊME fabrique que le CLI
            // (ApexSetup.Application.SetupApplicationServiceFactory),
            // jamais un type Core directement.
            var service = SetupApplicationServiceFactory.Create();
            var session = new SetupSession();
            var mainViewModel = new MainWindowViewModel(service, session);

            desktop.MainWindow = new MainWindow { DataContext = mainViewModel };
        }

        base.OnFrameworkInitializationCompleted();
    }
}
