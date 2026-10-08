using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard;

/// <summary>
/// Racine de composition PARTAGÉE par la fenêtre et par le mode sans fenêtre (<c>--status-json</c>,
/// <c>--diagnostic</c>) : les deux mesurent avec exactement le même code. Uniquement des
/// implémentations réelles : hôte WSL sous Windows, hôte local ailleurs.
/// </summary>
public sealed class PanelComposition
{
    private PanelComposition(FileLogSink log, Redactor redactor, JsonSettingsStore settings, PanelStatusService status, PanelControl control,
        RuntimePreparer? preparer)
    {
        Log = log;
        Redactor = redactor;
        Settings = settings;
        Status = status;
        Control = control;
        Preparer = preparer;
    }

    public FileLogSink Log { get; }
    public Redactor Redactor { get; }
    public JsonSettingsStore Settings { get; }
    public PanelStatusService Status { get; }

    /// <summary>Démarrer / arrêter / ouvrir APEX — toujours sur un geste de l'utilisateur.</summary>
    public PanelControl Control { get; }

    /// <summary>Mode Windows natif : « Préparer APEX » et « Installer le modèle » ; <c>null</c> dans les modes WSL et Linux.</summary>
    public RuntimePreparer? Preparer { get; }

    public static PanelComposition Create()
    {
        var redactor = new Redactor();
        var log = new FileLogSink(PanelInfo.LogDirectory, redactor);
        var runner = new RealProcessRunner();
        // APEX autonome installé à côté du Panel (app\apex) : mode Windows natif, sans WSL ni Docker. Sinon,
        // comme avant : backend dans WSL (Windows) ou sur cette machine (Linux).
        var native = NativeRuntime.Detect(PanelInfo.InstallDirectory);
        IBackendHost host = native is not null ? new NativeBackendHost(native)
            : OperatingSystem.IsWindows() ? new WslBackendHost(runner, new WindowsWslRegistry())
            : new LocalBackendHost();
        var client = new StatusClient(new HttpClient());
        var status = new PanelStatusService(host, client);
        status.SecretRead += redactor.Register;
        var launcher = new BackendLauncher(runner, runner, wsl: native is null && OperatingSystem.IsWindows(), native: native);
        var control = new PanelControl(launcher, client, new SystemBrowserLauncher());
        var preparer = native is null ? null : new RuntimePreparer(native, new HttpClient { Timeout = TimeSpan.FromMinutes(30) }, runner);
        return new PanelComposition(log, redactor, new JsonSettingsStore(PanelInfo.SettingsFile), status, control, preparer);
    }

    public DiagnosticContext DiagnosticContext(string? settingsProblem) => new(
        PanelInfo.OperatingSystem, PanelInfo.Runtime, PanelInfo.InstallDirectory, PanelInfo.SettingsFile,
        Log.Folder, Log.FileProblem, settingsProblem);
}
