using ApexSetup.Dashboard.ViewModels;
using Avalonia.Controls;

namespace ApexSetup.Dashboard.Views;

public partial class MainWindow : Window
{
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
            if (DataContext is MainWindowViewModel vm)
            {
                vm.CloseWindowRequested += (_, _) => Close();
            }
        };
    }
}

/// <summary>Presse-papiers réel de la fenêtre.</summary>
public sealed class WindowClipboard(TopLevel topLevel) : IClipboardService
{
    public Task SetTextAsync(string text) =>
        topLevel.Clipboard?.SetTextAsync(text) ?? throw new InvalidOperationException("presse-papiers indisponible");
}
