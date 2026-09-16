namespace ApexSetup.Dashboard.Services;

/// <summary>Mission §11 — n'ouvre le navigateur qu'après confirmation HTTP réelle (jamais appelé avant, voir <see cref="ApexOrchestrator"/>).</summary>
public interface IBrowserLauncher
{
    bool Open(string url);
}

/// <summary>
/// <c>UseShellExecute=true</c> sur une URL déjà construite depuis des
/// valeurs de configuration validées (jamais une entrée utilisateur libre
/// injectée telle quelle) — même précédent déjà établi dans ce dépôt pour
/// <c>Process.Start(UseShellExecute=true, Verb="runas")</c>
/// (<c>ApexSetup.Launcher.RealProcessLauncher.RelaunchElevated</c>) : la
/// SEULE autre utilisation de ce mode dans setup/, ici pour laisser Windows
/// résoudre le navigateur par défaut de l'utilisateur plutôt que d'en
/// deviner un chemin.
/// </summary>
public sealed class RealBrowserLauncher : IBrowserLauncher
{
    public bool Open(string url)
    {
        try
        {
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
            return true;
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            return false;
        }
    }
}
