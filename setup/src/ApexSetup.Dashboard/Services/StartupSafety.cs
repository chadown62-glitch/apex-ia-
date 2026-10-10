using System.Diagnostics;
using System.Runtime.InteropServices;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Ce qui se passe AVANT ou EN DEHORS de la fenêtre (0.4.3). Constaté sur Windows réel (10/10/2026, 0.4.2) :
/// « je double-clique sur l'icône d'APEX et rien ne se passe ». Un lancement ne doit jamais finir en silence :
/// <list type="bullet">
/// <item>plantage au démarrage : écrit dans le journal du Panel ET annoncé dans une boîte de message Windows
/// (qui ne dépend pas de l'interface graphique, elle-même peut-être en cause) ;</item>
/// <item>fenêtre déjà ouverte mais bloquée : le double-clic suivant le dit et propose de la fermer.</item>
/// </list>
/// </summary>
public static class StartupSafety
{
    private const uint MbOk = 0x0;
    private const uint MbYesNo = 0x4;
    private const uint MbIconError = 0x10;
    private const uint MbIconWarning = 0x30;
    private const uint MbSetForeground = 0x10000;
    private const uint MbTopMost = 0x40000;
    private const int IdYes = 6;

    /// <summary>Journal du Panel, utilisable avant la fenêtre (même dossier, même fichier du jour que la fenêtre).</summary>
    public static FileLogSink EarlyLog() => new(PanelInfo.LogDirectory, new Redactor());

    /// <summary>Un plantage : journalisé (pile complète) puis annoncé à l'utilisateur, avec où trouver le journal.</summary>
    public static void ReportCrash(Exception? exception, string when)
    {
        var log = EarlyLog();
        log.Log($"PLANTAGE du Panel ({when}) : {exception?.ToString() ?? "erreur inconnue"}");
        Show(
            "APEX n'a pas pu s'ouvrir.\n\n"
            + $"Erreur : {exception?.GetType().Name ?? "inconnue"} — {exception?.Message}\n\n"
            + $"Le détail est dans le journal :\n{log.CurrentFile}\n\n"
            + "Envoyez ce fichier pour le diagnostic.",
            MbOk | MbIconError);
    }

    /// <summary>
    /// La fenêtre d'APEX déjà ouverte ne répond pas : propose de la fermer. Vrai = l'utilisateur accepte.
    /// </summary>
    public static bool AskToCloseUnresponsive(IReadOnlyList<int> pids) =>
        Show(
            "APEX est déjà ouvert, mais sa fenêtre ne répond pas"
            + (pids.Count > 0 ? $" (processus {string.Join(", ", pids)})" : "") + ".\n\n"
            + "Fermer cette fenêtre bloquée et rouvrir APEX ?\n\n"
            + "APEX lui-même (le moteur, s'il tourne) n'est pas arrêté ; vos conversations et réglages sont conservés.",
            MbYesNo | MbIconWarning) == IdYes;

    public static void Inform(string message) => Show(message, MbOk | MbIconWarning);

    /// <summary>Les autres processus de CE Panel (même exécutable), jamais celui-ci ni le moteur d'APEX (python.exe).</summary>
    public static IReadOnlyList<Process> OtherPanels()
    {
        var self = Environment.ProcessId;
        var path = Environment.ProcessPath;
        var others = new List<Process>();
        foreach (var process in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(path ?? "APEX")))
        {
            if (process.Id == self)
            {
                process.Dispose();
                continue;
            }
            try
            {
                if (path is null || string.Equals(process.MainModule?.FileName, path, StringComparison.OrdinalIgnoreCase))
                {
                    others.Add(process);
                    continue;
                }
            }
            catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
            {
                // Processus d'un autre utilisateur ou déjà terminé : jamais touché.
            }
            process.Dispose();
        }
        return others;
    }

    /// <summary>Ferme de force les fenêtres bloquées ; retourne ce qui a été fait (pour le journal).</summary>
    public static string Terminate(IReadOnlyList<Process> panels)
    {
        var done = new List<string>();
        foreach (var process in panels)
        {
            try
            {
                process.Kill(entireProcessTree: false); // jamais le moteur d'APEX lancé par ce Panel
                done.Add(process.WaitForExit(10_000) ? $"{process.Id} fermé" : $"{process.Id} ne se ferme pas");
            }
            catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
            {
                done.Add($"{process.Id} : {ex.Message}");
            }
            finally
            {
                process.Dispose();
            }
        }
        return done.Count == 0 ? "aucun processus trouvé" : string.Join(", ", done);
    }

    private static int Show(string text, uint flags)
    {
        if (!OperatingSystem.IsWindows() || Environment.GetEnvironmentVariable("APEX_PANEL_NO_DIALOG") == "1")
        {
            Console.Error.WriteLine(text);
            return 0;
        }
        return MessageBoxW(IntPtr.Zero, text, "APEX", flags | MbSetForeground | MbTopMost);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);
}
