using Avalonia.Controls;
using ApexSetup.Gui.ViewModels;

namespace ApexSetup.Gui.Views;

/// <summary>
/// TROUVÉ EN REVUE ADVERSARIALE PHASE 10 : rien n'interceptait la
/// fermeture de la fenêtre pendant une installation réellement en cours —
/// fermer via le bouton OS de fermeture abandonnait la tâche d'installation
/// en arrière-plan sans jamais signaler son annulation au
/// <c>CancellationToken</c> déjà propagé jusqu'au pipeline Core (verrou
/// d'installation potentiellement orphelin, aucun avertissement affiché).
/// Corrigé : la première tentative de fermeture pendant une installation
/// est BLOQUÉE et déclenche une annulation réelle et coopérative
/// (<see cref="InstallProgressViewModel.RequestCancel"/> — jamais un
/// <c>Process.Kill</c> ou une simple disparition de fenêtre) ; la fenêtre
/// se ferme ELLE-MÊME automatiquement dès qu'<c>InstallCommand.IsRunning</c>
/// redevient faux (l'installation a eu l'occasion de s'arrêter proprement
/// entre deux étapes du pipeline).
/// </summary>
public partial class MainWindow : Window
{
    private bool _closeConfirmed;

    public MainWindow() => InitializeComponent();

    protected override void OnClosing(WindowClosingEventArgs e)
    {
        if (!_closeConfirmed && DataContext is MainWindowViewModel vm && vm.InstallScreen.InstallCommand.IsRunning)
        {
            e.Cancel = true;
            vm.InstallScreen.InstallCommand.CanExecuteChanged += OnInstallCommandStateChanged;
            vm.InstallScreen.RequestCancel();
            return;
        }
        base.OnClosing(e);
    }

    private void OnInstallCommandStateChanged(object? sender, EventArgs e)
    {
        if (DataContext is not MainWindowViewModel vm || vm.InstallScreen.InstallCommand.IsRunning)
        {
            return;
        }
        vm.InstallScreen.InstallCommand.CanExecuteChanged -= OnInstallCommandStateChanged;
        _closeConfirmed = true;
        Close();
    }
}
