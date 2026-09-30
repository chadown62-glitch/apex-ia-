using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard;

/// <summary>
/// Racine de composition PARTAGÉE par la fenêtre et par le mode sans fenêtre (<c>--status-json</c>,
/// <c>--diagnostic</c>) : les deux mesurent avec exactement le même code. Uniquement des
/// implémentations réelles : hôte WSL sous Windows, hôte local ailleurs.
/// </summary>
public sealed class PanelComposition
{
    private PanelComposition(FileLogSink log, Redactor redactor, JsonSettingsStore settings, PanelStatusService status)
    {
        Log = log;
        Redactor = redactor;
        Settings = settings;
        Status = status;
    }

    public FileLogSink Log { get; }
    public Redactor Redactor { get; }
    public JsonSettingsStore Settings { get; }
    public PanelStatusService Status { get; }

    public static PanelComposition Create()
    {
        var redactor = new Redactor();
        var log = new FileLogSink(PanelInfo.LogDirectory, redactor);
        IBackendHost host = OperatingSystem.IsWindows()
            ? new WslBackendHost(new RealProcessRunner(), new WindowsWslRegistry())
            : new LocalBackendHost();
        var status = new PanelStatusService(host, new StatusClient(new HttpClient()));
        status.SecretRead += redactor.Register;
        return new PanelComposition(log, redactor, new JsonSettingsStore(PanelInfo.SettingsFile), status);
    }

    public DiagnosticContext DiagnosticContext(string? settingsProblem) => new(
        PanelInfo.OperatingSystem, PanelInfo.Runtime, PanelInfo.InstallDirectory, PanelInfo.SettingsFile,
        Log.Folder, Log.FileProblem, settingsProblem);
}
