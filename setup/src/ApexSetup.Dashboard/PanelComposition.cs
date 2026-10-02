using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard;

/// <summary>
/// Racine de composition PARTAGÉE par la fenêtre et par le mode sans fenêtre (<c>--status-json</c>,
/// <c>--diagnostic</c>) : les deux mesurent avec exactement le même code. Uniquement des
/// implémentations réelles : hôte WSL sous Windows, hôte local ailleurs.
/// </summary>
public sealed class PanelComposition
{
    private PanelComposition(FileLogSink log, Redactor redactor, JsonSettingsStore settings, PanelStatusService status, PanelControl control)
    {
        Log = log;
        Redactor = redactor;
        Settings = settings;
        Status = status;
        Control = control;
    }

    public FileLogSink Log { get; }
    public Redactor Redactor { get; }
    public JsonSettingsStore Settings { get; }
    public PanelStatusService Status { get; }

    /// <summary>Démarrer / arrêter / ouvrir APEX — toujours sur un geste de l'utilisateur.</summary>
    public PanelControl Control { get; }

    public static PanelComposition Create()
    {
        var redactor = new Redactor();
        var log = new FileLogSink(PanelInfo.LogDirectory, redactor);
        var runner = new RealProcessRunner();
        IBackendHost host = OperatingSystem.IsWindows()
            ? new WslBackendHost(runner, new WindowsWslRegistry())
            : new LocalBackendHost();
        var client = new StatusClient(new HttpClient());
        var status = new PanelStatusService(host, client);
        status.SecretRead += redactor.Register;
        var control = new PanelControl(new BackendLauncher(runner, runner, wsl: OperatingSystem.IsWindows()), client, new SystemBrowserLauncher());
        return new PanelComposition(log, redactor, new JsonSettingsStore(PanelInfo.SettingsFile), status, control);
    }

    public DiagnosticContext DiagnosticContext(string? settingsProblem) => new(
        PanelInfo.OperatingSystem, PanelInfo.Runtime, PanelInfo.InstallDirectory, PanelInfo.SettingsFile,
        Log.Folder, Log.FileProblem, settingsProblem);
}
