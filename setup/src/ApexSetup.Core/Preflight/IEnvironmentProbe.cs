namespace ApexSetup.Core.Preflight;

/// <summary>
/// Abstraction des faits environnementaux locaux nécessaires au Preflight
/// (espace disque, permissions) — séparée de <c>RuntimeAdapter</c> (qui
/// interroge le Runtime) : ceci interroge la MACHINE WINDOWS locale sur
/// laquelle Setup tourne. Une seule implémentation réelle
/// (<see cref="RealEnvironmentProbe"/>, testée contre le vrai système de
/// fichiers) ; une implémentation de test pour les scénarios impossibles à
/// provoquer de façon fiable (permissions refusées de façon portable).
/// </summary>
public interface IEnvironmentProbe
{
    /// <summary>Octets disponibles sur le volume contenant <paramref name="path"/>.</summary>
    long GetAvailableDiskBytes(string path);

    /// <summary>
    /// True si un fichier peut réellement être créé puis supprimé dans
    /// <paramref name="directoryPath"/> (créé s'il n'existe pas encore) —
    /// jamais une simple lecture d'ACL déclarative, une preuve empirique.
    /// </summary>
    bool CanWriteTo(string directoryPath);
}

public sealed class RealEnvironmentProbe : IEnvironmentProbe
{
    public long GetAvailableDiskBytes(string path)
    {
        var fullPath = Path.GetFullPath(path);
        var root = Path.GetPathRoot(fullPath);
        if (string.IsNullOrEmpty(root))
        {
            throw new ArgumentException($"impossible de résoudre la racine du volume pour « {path} »", nameof(path));
        }
        return new DriveInfo(root).AvailableFreeSpace;
    }

    public bool CanWriteTo(string directoryPath)
    {
        try
        {
            Directory.CreateDirectory(directoryPath);
            var probeFile = Path.Combine(directoryPath, $".apex-setup-write-probe-{Guid.NewGuid():N}");
            File.WriteAllText(probeFile, string.Empty);
            File.Delete(probeFile);
            return true;
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            return false;
        }
    }
}
