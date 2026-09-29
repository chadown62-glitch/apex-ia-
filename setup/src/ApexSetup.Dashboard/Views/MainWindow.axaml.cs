using ApexSetup.Dashboard.ViewModels;
using Avalonia.Controls;

namespace ApexSetup.Dashboard.Views;

public partial class MainWindow : Window
{
    public MainWindow() => InitializeComponent();
}

/// <summary>Presse-papiers réel de la fenêtre.</summary>
public sealed class WindowClipboard(TopLevel topLevel) : IClipboardService
{
    public Task SetTextAsync(string text) =>
        topLevel.Clipboard?.SetTextAsync(text) ?? throw new InvalidOperationException("presse-papiers indisponible");
}
