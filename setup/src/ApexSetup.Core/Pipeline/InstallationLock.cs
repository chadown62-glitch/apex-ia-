namespace ApexSetup.Core.Pipeline;

/// <summary>
/// TROUVÉ EN REVUE ADVERSARIALE (§7 Concurrence) : rien n'empêchait deux
/// exécutions du pipeline de cibler la même destination simultanément —
/// résultat indéterministe garanti (deux Download/Installation en
/// parallèle sur les mêmes fichiers). Verrou exclusif par fichier
/// (<c>FileShare.None</c>, propriété du système de fichiers, pas une
/// convention applicative) : la seconde exécution reçoit un refus
/// EXPLICITE plutôt que de démarrer.
/// </summary>
public sealed class InstallationLock : IDisposable
{
    private readonly FileStream _stream;
    private readonly string _path;
    private bool _disposed;

    private InstallationLock(FileStream stream, string path)
    {
        _stream = stream;
        _path = path;
    }

    public const string LockFileName = ".apex-setup.lock";

    /// <summary>Tente d'acquérir le verrou — retourne null (jamais une exception) si déjà tenu par une autre exécution.</summary>
    public static InstallationLock? TryAcquire(string destinationDirectory)
    {
        Directory.CreateDirectory(destinationDirectory);
        var lockPath = Path.Combine(destinationDirectory, LockFileName);
        try
        {
            var stream = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            return new InstallationLock(stream, lockPath);
        }
        catch (IOException)
        {
            return null;
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _stream.Dispose();
        try
        {
            File.Delete(_path);
        }
        catch (IOException)
        {
            // Best-effort — la présence résiduelle du fichier de verrou ne
            // bloque personne : FileShare.None protège sur le HANDLE, pas
            // sur l'existence du fichier lui-même.
        }
    }
}
