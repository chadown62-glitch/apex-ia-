using System.Text;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Contexte local du diagnostic (tout ce qui n'est pas une mesure du backend).</summary>
public sealed record DiagnosticContext(
    string OperatingSystem, string Runtime, string InstallDirectory, string SettingsFile, string LogDirectory,
    string? LogProblem, string? SettingsProblem);

/// <summary>
/// Texte du diagnostic affiché et copié : uniquement des mesures et des chemins, jamais le jeton
/// (le snapshot ne le contient pas ; le texte passe en plus par le <see cref="Redactor"/>).
/// </summary>
public static class DiagnosticReport
{
    public static string Build(PanelSnapshot snapshot, DiagnosticContext context, Redactor redactor)
    {
        var v = snapshot.Versions;
        var b = snapshot.Backend;
        var text = new StringBuilder();
        text.AppendLine("APEX — diagnostic du Panel");
        text.AppendLine($"Mesuré le            : {snapshot.MeasuredAt:yyyy-MM-dd HH:mm:ss zzz} (durée {snapshot.Duration.TotalMilliseconds:0} ms)");
        text.AppendLine($"Verdict              : {snapshot.Headline} ({snapshot.Cause})");
        text.AppendLine($"Consigne             : {snapshot.Guidance}");
        text.AppendLine();
        text.AppendLine($"Système              : {context.OperatingSystem}");
        text.AppendLine($"Runtime .NET         : {context.Runtime}");
        text.AppendLine($"Version du Panel     : {v.PanelVersion} (protocole {v.PanelProtocol})");
        text.AppendLine($"Version APEX backend : {v.BackendApexVersion ?? "inconnue"}");
        text.AppendLine($"Version API          : {v.BackendApiVersion ?? "inconnue"}");
        text.AppendLine($"Protocole backend    : {v.BackendProtocol?.ToString() ?? "inconnu"}");
        text.AppendLine($"WSL                  : {(snapshot.WslApplicable ? $"{Row(snapshot, ComponentKey.Wsl)} (distribution : {snapshot.WslDistro ?? "par défaut"})" : "sans objet (hôte Linux natif)")}");
        text.AppendLine($"Backend enregistré   : port {b.Port?.ToString() ?? "?"} · pid {b.Pid?.ToString() ?? "?"} · démarré {b.StartedAt?.ToString("yyyy-MM-dd HH:mm:ss zzz") ?? "?"} · projet {b.ProjectRoot ?? "?"}");
        text.AppendLine();
        foreach (var key in Enum.GetValues<ComponentKey>())
        {
            if (key == ComponentKey.Wsl && !snapshot.WslApplicable)
            {
                continue;
            }
            text.AppendLine($"{ComponentKeys.Label(key),-20} : {Row(snapshot, key)}");
        }
        text.AppendLine($"{"GPU",-20} : non exposé par le backend (non mesuré par le Panel)");
        text.AppendLine();
        text.AppendLine($"Installation         : {context.InstallDirectory}");
        text.AppendLine($"Paramètres           : {context.SettingsFile}{(context.SettingsProblem is null ? "" : $" — {context.SettingsProblem}")}");
        text.AppendLine($"Journaux             : {context.LogDirectory}{(context.LogProblem is null ? "" : $" — {context.LogProblem}")}");
        return redactor.Redact(text.ToString());
    }

    private static string Row(PanelSnapshot snapshot, ComponentKey key)
    {
        var row = snapshot.Get(key);
        return row is null ? "non mesuré" : $"{StatusEngine.Label(row.Status)} — {row.Detail}";
    }
}
