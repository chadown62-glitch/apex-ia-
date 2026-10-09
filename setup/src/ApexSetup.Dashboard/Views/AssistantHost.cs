using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Platform;
using Microsoft.Web.WebView2.Core;

namespace ApexSetup.Dashboard.Views;

/// <summary>
/// L'interface de l'assistant APEX DANS la fenêtre d'APEX.exe (0.4.1, demande de l'utilisateur : « une interface
/// 100 % dans le logiciel ») : le composant Microsoft Edge WebView2 de Windows, hébergé dans une fenêtre native
/// enfant. Seule l'interface locale d'APEX y est affichée (même origine que l'adresse d'ouverture) ; tout autre
/// lien s'ouvre dans le navigateur du système, jamais dans la fenêtre d'APEX.
/// Hors Windows (tests, Linux), rien n'est créé. Composant absent ou en erreur : <see cref="Failed"/> donne la
/// raison, le Panel l'affiche et propose le navigateur — jamais une fenêtre vide sans explication.
/// </summary>
public sealed class AssistantHost : NativeControlHost
{
    public static readonly StyledProperty<string?> UrlProperty =
        AvaloniaProperty.Register<AssistantHost, string?>(nameof(Url));

    /// <summary>Adresse d'ouverture (lien à usage unique de l'interface locale) ; <c>null</c> = rien à afficher.</summary>
    public string? Url
    {
        get => GetValue(UrlProperty);
        set => SetValue(UrlProperty, value);
    }

    /// <summary>Dossier des données du composant (cookies de session, cache) : jamais à côté de l'exécutable.</summary>
    public string? UserDataFolder { get; set; }

    /// <summary>Le composant n'a pas pu être créé ; la raison est lisible par l'utilisateur.</summary>
    public event EventHandler<string>? Failed;

    private WebViewSession? _session;

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property == UrlProperty && OperatingSystem.IsWindows())
        {
            _session?.Navigate(Url);
        }
        else if (change.Property == BoundsProperty && OperatingSystem.IsWindows())
        {
            _session?.Resize(Bounds.Size, TopLevel.GetTopLevel(this)?.RenderScaling ?? 1.0);
        }
    }

    protected override IPlatformHandle CreateNativeControlCore(IPlatformHandle parent)
    {
        if (!OperatingSystem.IsWindows())
        {
            return base.CreateNativeControlCore(parent);
        }
        var session = new WebViewSession(parent.Handle, UserDataFolder, reason => Failed?.Invoke(this, reason));
        _session = session;
        _ = session.InitializeAsync(Url, Bounds.Size, TopLevel.GetTopLevel(this)?.RenderScaling ?? 1.0);
        return new PlatformHandle(session.Handle, "HWND");
    }

    protected override void DestroyNativeControlCore(IPlatformHandle control)
    {
        if (!OperatingSystem.IsWindows())
        {
            base.DestroyNativeControlCore(control);
            return;
        }
        _session?.Dispose();
        _session = null;
    }
}

/// <summary>Une fenêtre enfant Win32 et le contrôleur WebView2 qu'elle porte.</summary>
[SupportedOSPlatform("windows")]
internal sealed class WebViewSession : IDisposable
{
    private const int WsChild = 0x40000000;
    private const int WsVisible = 0x10000000;
    private const int WsClipChildren = 0x02000000;

    private readonly string? _userDataFolder;
    private readonly Action<string> _failed;
    private CoreWebView2Controller? _controller;
    private string? _pendingUrl;
    private string? _origin;
    private bool _disposed;

    public WebViewSession(IntPtr parent, string? userDataFolder, Action<string> failed)
    {
        _userDataFolder = userDataFolder;
        _failed = failed;
        Handle = CreateWindowExW(0, "Static", string.Empty, WsChild | WsVisible | WsClipChildren, 0, 0, 1, 1,
            parent, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
    }

    public IntPtr Handle { get; }

    public async Task InitializeAsync(string? url, Size size, double scale)
    {
        _pendingUrl = url;
        if (Handle == IntPtr.Zero)
        {
            _failed($"fenêtre d'affichage impossible à créer (erreur Win32 {Marshal.GetLastWin32Error()})");
            return;
        }
        try
        {
            var environment = await CoreWebView2Environment.CreateAsync(null, _userDataFolder);
            var controller = await environment.CreateCoreWebView2ControllerAsync(Handle);
            if (_disposed)
            {
                controller.Close();
                return;
            }
            _controller = controller;
            var core = controller.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = true;
            core.NewWindowRequested += (_, e) =>
            {
                e.Handled = true; // jamais une seconde fenêtre de navigateur dans APEX
                if (SameOrigin(e.Uri))
                {
                    core.Navigate(e.Uri);
                }
                else
                {
                    OpenOutside(e.Uri);
                }
            };
            core.NavigationStarting += (_, e) =>
            {
                if (!SameOrigin(e.Uri))
                {
                    e.Cancel = true; // seule l'interface locale d'APEX s'affiche ici
                    OpenOutside(e.Uri);
                }
            };
            Resize(size, scale);
            Navigate(_pendingUrl);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            _failed("le composant Microsoft Edge WebView2 n'est pas installé sur ce PC");
        }
        catch (Exception ex)
        {
            _failed($"affichage intégré impossible ({ex.GetType().Name}: {ex.Message})");
        }
    }

    public void Navigate(string? url)
    {
        _pendingUrl = url;
        if (_controller is null || string.IsNullOrEmpty(url) || !Uri.TryCreate(url, UriKind.Absolute, out var uri))
        {
            return;
        }
        _origin = uri.GetLeftPart(UriPartial.Authority);
        _controller.CoreWebView2.Navigate(url);
    }

    public void Resize(Size size, double scale)
    {
        if (_controller is null)
        {
            return;
        }
        var width = Math.Max(1, (int)Math.Round(size.Width * scale));
        var height = Math.Max(1, (int)Math.Round(size.Height * scale));
        _controller.Bounds = new System.Drawing.Rectangle(0, 0, width, height);
    }

    private bool SameOrigin(string url) =>
        _origin is not null && Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && string.Equals(uri.GetLeftPart(UriPartial.Authority), _origin, StringComparison.OrdinalIgnoreCase);

    private static void OpenOutside(string url)
    {
        if (Uri.TryCreate(url, UriKind.Absolute, out var uri) && (uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp))
        {
            try
            {
                using var process = Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
            }
            catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
            {
                // Aucun navigateur : le lien n'est simplement pas ouvert.
            }
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _controller?.Close();
        _controller = null;
        if (Handle != IntPtr.Zero)
        {
            DestroyWindow(Handle);
        }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateWindowExW(int exStyle, string className, string windowName, int style, int x, int y,
        int width, int height, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyWindow(IntPtr hwnd);
}
