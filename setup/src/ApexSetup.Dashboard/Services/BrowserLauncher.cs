using System.Diagnostics;

namespace ApexSetup.Dashboard.Services;

/// <summary>Ouvre une adresse dans le navigateur par défaut (« Ouvrir APEX »).</summary>
public interface IBrowserLauncher
{
    /// <summary><c>null</c> si ouvert ; sinon la raison (jamais l'adresse : elle contient un code d'ouverture).</summary>
    string? Open(string url);
}

/// <summary>
/// Windows : le gestionnaire d'URL du système (<c>UseShellExecute</c>, seule façon d'atteindre le
/// navigateur par défaut) ; ailleurs : <c>xdg-open</c>, argv direct. N'accepte qu'une adresse
/// <c>http://</c> de la boucle locale construite par <see cref="StatusClient.CreateLaunchCodeAsync"/> —
/// jamais une adresse arbitraire, jamais un fichier ou un programme.
/// </summary>
public sealed class SystemBrowserLauncher : IBrowserLauncher
{
    public string? Open(string url)
    {
        if (!IsLocalHttp(url))
        {
            return "adresse refusée (seule l'interface APEX de cette machine peut être ouverte)";
        }
        try
        {
            var startInfo = OperatingSystem.IsWindows()
                ? new ProcessStartInfo(url) { UseShellExecute = true }
                : new ProcessStartInfo("xdg-open") { UseShellExecute = false, ArgumentList = { url } };
            using var process = Process.Start(startInfo);
            return null;
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            return $"navigateur introuvable ({ex.GetType().Name})";
        }
    }

    public static bool IsLocalHttp(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri)
        && uri.Scheme == Uri.UriSchemeHttp
        && System.Net.IPAddress.TryParse(uri.Host.Trim('[', ']'), out var address)
        && System.Net.IPAddress.IsLoopback(address)
        && uri.AbsolutePath == "/ui/login";
}
