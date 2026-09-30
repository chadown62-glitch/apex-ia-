using Microsoft.Win32;

namespace ApexSetup.Dashboard.Services;

/// <summary>Distribution WSL enregistrée pour l'utilisateur courant (source structurée, jamais traduite).</summary>
/// <param name="Version">1 ou 2 (WSL 1 / WSL 2) ; <c>null</c> si Windows ne l'indique pas.</param>
public sealed record WslRegisteredDistro(string Name, int? Version, bool IsDefault);

/// <summary>
/// Liste STRUCTURÉE des distributions WSL : noms, distribution par défaut et version WSL, sans aucun
/// texte traduit ni format de sortie de <c>wsl.exe</c> à interpréter (défaut W3 de l'audit).
/// </summary>
public interface IWslRegistry
{
    /// <summary>
    /// Distributions enregistrées ; liste vide si aucune ; <c>null</c> si la source est illisible —
    /// l'appelant revient alors à la sortie de <c>wsl.exe -l -v</c>, jamais à une supposition.
    /// </summary>
    IReadOnlyList<WslRegisteredDistro>? ReadDistributions();
}

/// <summary>
/// <c>HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss</c> : une sous-clé par distribution
/// (<c>DistributionName</c>, <c>Version</c>, <c>State</c>) et la valeur <c>DefaultDistribution</c>.
/// Lecture seule ; ne démarre rien.
/// </summary>
public sealed class WindowsWslRegistry : IWslRegistry
{
    internal const string LxssKey = @"Software\Microsoft\Windows\CurrentVersion\Lxss";

    public IReadOnlyList<WslRegisteredDistro>? ReadDistributions()
    {
        if (!OperatingSystem.IsWindows())
        {
            return null;
        }
        try
        {
            using var lxss = Registry.CurrentUser.OpenSubKey(LxssKey);
            if (lxss is null)
            {
                return []; // aucune distribution n'a jamais été enregistrée pour cet utilisateur
            }
            var defaultId = lxss.GetValue("DefaultDistribution") as string;
            var distros = new List<WslRegisteredDistro>();
            foreach (var id in lxss.GetSubKeyNames())
            {
                using var key = lxss.OpenSubKey(id);
                var name = key?.GetValue("DistributionName") as string;
                // State 1 = installée ; les autres états (installation ou désinstallation en cours) ne sont
                // pas des distributions utilisables.
                if (string.IsNullOrWhiteSpace(name) || key!.GetValue("State") is int state && state != 1)
                {
                    continue;
                }
                var version = key.GetValue("Version") is int v ? v : (int?)null;
                distros.Add(new WslRegisteredDistro(name, version, string.Equals(id, defaultId, StringComparison.OrdinalIgnoreCase)));
            }
            return distros;
        }
        catch (Exception ex) when (ex is System.Security.SecurityException or UnauthorizedAccessException or IOException)
        {
            return null;
        }
    }
}
