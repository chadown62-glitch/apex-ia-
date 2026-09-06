using System.Runtime.Versioning;
using System.Security.Principal;

namespace ApexSetup.Launcher.Elevation;

/// <summary>
/// Implémentation réelle — seule pièce de <c>ApexSetup.Launcher</c> qui
/// touche directement l'API Windows d'identité de processus. Jamais
/// instanciée par les tests unitaires (voir
/// <c>ApexSetup.Launcher.Tests/FakeElevationContext.cs</c>) : ce fichier ne
/// peut pas être exécuté ni vérifié dans ce bac à sable Linux — limite
/// assumée, documentée dans le rapport final N+15, même précédent que
/// <c>setup/docs/GUI_ARCHITECTURE.md</c> pour Avalonia.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsElevationContext : IElevationContext
{
    public bool IsElevated
    {
        get
        {
            using var identity = WindowsIdentity.GetCurrent();
            var principal = new WindowsPrincipal(identity);
            return principal.IsInRole(WindowsBuiltInRole.Administrator);
        }
    }

    public bool RequiresElevationFor(string targetPath)
    {
        if (IsElevated)
        {
            // Déjà élevé : peu importe ce que l'écriture réelle dirait,
            // aucune élévation supplémentaire n'est jamais demandée
            // (règle absolue « pas de boucle UAC »).
            return false;
        }

        // Vérification EMPIRIQUE (jamais une supposition sur le chemin,
        // ex. « C:\Program Files\... nécessite toujours l'admin ») :
        // certaines installations utilisateur (§C Mode Utilisateur) vivent
        // dans un répertoire inscriptible sans élévation même sous
        // Program Files si les ACL locales l'autorisent.
        try
        {
            Directory.CreateDirectory(targetPath);
            var probePath = Path.Combine(targetPath, $".apex-launcher-write-probe-{Guid.NewGuid():N}");
            File.WriteAllBytes(probePath, []);
            File.Delete(probePath);
            return false;
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            return true;
        }
    }
}
