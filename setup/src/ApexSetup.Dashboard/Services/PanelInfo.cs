using System.Reflection;
using System.Runtime.InteropServices;

namespace ApexSetup.Dashboard.Services;

/// <summary>Identité du Panel et emplacements de ses fichiers — séparés : configuration (roaming), journaux (local), installation (binaire).</summary>
public static class PanelInfo
{
    /// <summary>Version centrale de <c>setup/Directory.Build.props</c> (jamais un 1.0.0.0 par défaut).</summary>
    public static string Version { get; } =
        typeof(PanelInfo).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0]
        ?? typeof(PanelInfo).Assembly.GetName().Version?.ToString(3)
        ?? "0.0.0";

    /// <summary>Surchargeable (tests, diagnostic) ; sinon <c>%APPDATA%\APEX</c> (Linux : <c>~/.config/APEX</c>).</summary>
    public static string ConfigDirectory =>
        Environment.GetEnvironmentVariable("APEX_PANEL_CONFIG_DIR")
        ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "APEX");

    /// <summary>Surchargeable ; sinon <c>%LOCALAPPDATA%\APEX\logs</c> (Linux : <c>~/.local/share/APEX/logs</c>).</summary>
    public static string LogDirectory =>
        Environment.GetEnvironmentVariable("APEX_PANEL_LOG_DIR")
        ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "APEX", "logs");

    public static string SettingsFile => Path.Combine(ConfigDirectory, "dashboard-settings.json");

    public static string InstallDirectory => AppContext.BaseDirectory;

    public static string OperatingSystem => RuntimeInformation.OSDescription;

    public static string Runtime => RuntimeInformation.FrameworkDescription;
}
