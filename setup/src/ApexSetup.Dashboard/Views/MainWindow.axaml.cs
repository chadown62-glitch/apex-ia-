using System.ComponentModel;
using ApexSetup.Dashboard.Services;
using ApexSetup.Dashboard.ViewModels;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Data;
using Avalonia.Threading;

namespace ApexSetup.Dashboard.Views;

public partial class MainWindow : Window
{
    private MainWindowViewModel? _vm;
    private AssistantHost? _assistantHost;

    public MainWindow()
    {
        InitializeComponent();
        // Mode natif : fermer pendant qu'APEX tourne DEMANDE quoi faire (arrêter ou laisser tourner).
        Closing += (_, e) =>
        {
            if (DataContext is MainWindowViewModel vm && vm.InterceptClose())
            {
                e.Cancel = true;
            }
        };
        DataContextChanged += (_, _) =>
        {
            if (_vm is not null)
            {
                _vm.PropertyChanged -= OnViewModelChanged;
            }
            _vm = DataContext as MainWindowViewModel;
            if (_vm is not null)
            {
                _vm.CloseWindowRequested += (_, _) => Close();
                _vm.PropertyChanged += OnViewModelChanged;
            }
        };
    }

    /// <summary>Sentinelle de la création du composant d'affichage (fournie par la racine de composition).</summary>
    public EmbeddedDisplayGuard? DisplayGuard { get; set; }

    /// <summary>
    /// 0.4.3 — le composant d'affichage (WebView2) n'est créé que lorsqu'il y a une page à montrer, jamais à
    /// l'ouverture de la fenêtre : la fenêtre s'affiche toujours d'abord, APEX démarré ou non. Il est retiré quand
    /// l'affichage intégré est abandonné (échec, plantage précédent), et recréé neuf sur « Réessayer dans la fenêtre ».
    /// </summary>
    private void OnViewModelChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is not (nameof(MainWindowViewModel.IsAssistantHostVisible) or nameof(MainWindowViewModel.IsEmbeddedAssistant)))
        {
            return;
        }
        if (!Dispatcher.UIThread.CheckAccess())
        {
            Dispatcher.UIThread.Post(() => OnViewModelChanged(sender, e));
            return;
        }
        if (_vm is null)
        {
            return;
        }
        if (!_vm.IsEmbeddedAssistant)
        {
            if (_assistantHost is not null)
            {
                AssistantSlot.Content = null; // détaché : le composant est fermé (DestroyNativeControlCore)
                _assistantHost = null;
            }
            return;
        }
        if (_vm.IsAssistantHostVisible && _assistantHost is null)
        {
            var vm = _vm;
            var host = new AssistantHost
            {
                Name = "AssistantView",
                // Cookies de session et cache du composant : avec les données d'APEX, jamais à côté de l'exécutable.
                UserDataFolder = Path.Combine(NativeRuntime.DefaultHome(), "webview"),
                DisplayGuard = DisplayGuard,
                Log = vm.LogLine,
            };
            host.Bind(AssistantHost.UrlProperty, new Binding(nameof(MainWindowViewModel.AssistantUrl)));
            host.Bind(IsVisibleProperty, new Binding(nameof(MainWindowViewModel.IsAssistantHostVisible)));
            host.Failed += (_, reason) => vm.ReportEmbeddedFailure(reason);
            _assistantHost = host;
            AssistantSlot.Content = host;
        }
    }
}

/// <summary>Presse-papiers réel de la fenêtre.</summary>
public sealed class WindowClipboard(TopLevel topLevel) : IClipboardService
{
    public Task SetTextAsync(string text) =>
        topLevel.Clipboard?.SetTextAsync(text) ?? throw new InvalidOperationException("presse-papiers indisponible");
}
