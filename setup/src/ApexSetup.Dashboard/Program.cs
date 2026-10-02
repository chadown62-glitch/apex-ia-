using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;
using Avalonia;

namespace ApexSetup.Dashboard;

public static class Program
{
    /// <summary>Codes de sortie du mode sans fenêtre (<c>--status-json</c>) : un par verdict.</summary>
    public static int ExitCode(PanelVerdict verdict) => verdict switch
    {
        PanelVerdict.Ready => 0,
        PanelVerdict.Degraded => 1,
        PanelVerdict.NotReady => 2,
        PanelVerdict.VersionMismatch => 3,
        PanelVerdict.Blocked => 4,
        _ => 5,
    };

    internal static SingleInstanceGuard? Guard { get; private set; }

    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Contains("--version"))
        {
            Console.WriteLine(PanelInfo.Version);
            return 0;
        }
        if (args.Contains("--status-json") || args.Contains("--diagnostic"))
        {
            return RunHeadlessAsync(args.Contains("--diagnostic")).GetAwaiter().GetResult();
        }
        // Mêmes actions que les boutons, sans fenêtre (automatisation, validation Windows réelle) :
        // mêmes services, même launcher, même Startup Gate.
        if (args.Contains("--start"))
        {
            return HeadlessActions.StartAsync().GetAwaiter().GetResult();
        }
        if (args.Contains("--stop"))
        {
            return HeadlessActions.StopAsync().GetAwaiter().GetResult();
        }
        if (args.Contains("--ui-url"))
        {
            return HeadlessActions.UiUrlAsync().GetAwaiter().GetResult();
        }

        // Un second double-clic ramène le Panel existant au premier plan au lieu d'en ouvrir un autre.
        Guard = SingleInstanceGuard.TryAcquire();
        if (Guard is null)
        {
            return 0;
        }
        try
        {
            return BuildAvaloniaApp(SoftwareRendering(args)).StartWithClassicDesktopLifetime(args);
        }
        finally
        {
            Guard.Dispose();
        }
    }

    /// <summary>Même mesure que la fenêtre, imprimée sur la sortie standard (diagnostic, tests, support).</summary>
    public static async Task<int> RunHeadlessAsync(bool diagnosticText)
    {
        var composition = PanelComposition.Create();
        var loaded = await composition.Settings.LoadAsync().ConfigureAwait(false);
        var snapshot = await composition.Status.MeasureAsync(loaded.Settings).ConfigureAwait(false);
        composition.Log.Log($"[sans fenêtre] {snapshot.Headline} ({snapshot.Cause})");
        var output = diagnosticText
            ? DiagnosticReport.Build(snapshot, composition.DiagnosticContext(loaded.Problem), composition.Redactor)
            : composition.Redactor.Redact(JsonSerializer.Serialize(snapshot, JsonOutput));
        Emit(output, Console.IsOutputRedirected, Console.OpenStandardOutput, Console.Out);
        return ExitCode(snapshot.Verdict);
    }

    /// <summary>
    /// Sortie redirigée (fichier, tube — <c>APEX.exe --diagnostic &gt; diagnostic.txt</c>) : UTF-8 sans BOM,
    /// quelle que soit la page de codes de la console. Constaté sous Wine (séquence de robustesse) : via
    /// <see cref="Console.Out"/>, les accents du diagnostic sortaient dans la page de codes OEM, illisibles
    /// dans un fichier UTF-8. Console interactive : inchangée (Unicode natif), et la page de codes de la
    /// console parente n'est jamais modifiée.
    /// </summary>
    internal static void Emit(string text, bool redirected, Func<Stream> openStandardOutput, TextWriter console)
    {
        if (!redirected)
        {
            console.WriteLine(text);
            console.Flush();
            return;
        }
        using var writer = new StreamWriter(openStandardOutput(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        writer.WriteLine(text);
        writer.Flush();
    }

    internal static readonly JsonSerializerOptions JsonOutput = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter() },
    };

    /// <summary>
    /// Rendu logiciel sur demande (<c>--software-rendering</c> ou <c>APEX_PANEL_RENDERING=software</c>) :
    /// recours documenté si la fenêtre reste noire (pilote graphique défaillant, bureau à distance,
    /// machine virtuelle sans accélération).
    /// </summary>
    private static bool SoftwareRendering(string[] args) =>
        args.Contains("--software-rendering")
        || string.Equals(Environment.GetEnvironmentVariable("APEX_PANEL_RENDERING"), "software", StringComparison.OrdinalIgnoreCase);

    /// <summary>Exposée séparément pour les tests headless — même patron qu'ApexSetup.Gui.Program.</summary>
    public static AppBuilder BuildAvaloniaApp() => BuildAvaloniaApp(false);

    public static AppBuilder BuildAvaloniaApp(bool softwareRendering)
    {
        var builder = AppBuilder.Configure<App>().UsePlatformDetect().WithInterFont().LogToTrace();
        if (softwareRendering)
        {
            builder = builder
                .With(new Win32PlatformOptions { RenderingMode = [Win32RenderingMode.Software] })
                .With(new X11PlatformOptions { RenderingMode = [X11RenderingMode.Software] });
        }
        return builder;
    }
}
