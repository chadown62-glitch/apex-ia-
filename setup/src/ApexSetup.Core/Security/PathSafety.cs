namespace ApexSetup.Core.Security;

/// <summary>
/// Levée quand un segment de chemin issu du manifeste/plan (donnée
/// externe, potentiellement compromise — voir revue adversariale §6) ne
/// peut pas être combiné en sécurité avec un répertoire de destination.
/// Jamais interceptée pour « corriger » silencieusement le chemin — un
/// artefact au nom dangereux doit faire échouer l'étape, jamais être
/// réécrit à la place de l'utilisateur.
/// </summary>
public sealed class UnsafePathException(string message) : Exception(message);

/// <summary>
/// TROUVÉ EN REVUE ADVERSARIALE (§6 Path Security) : <c>Path.Combine</c>
/// seul ne protège de RIEN — un second segment absolu (<c>/etc/passwd</c>,
/// <c>C:\Windows\System32\x</c>, UNC <c>\\serveur\partage</c>) fait
/// IGNORER le premier argument par le framework, et un segment contenant
/// <c>..</c> n'est jamais bloqué. Avant cette classe, <c>DownloadStage</c>
/// et <c>InstallationStage</c> combinaient directement
/// <c>artifact.ArtifactId</c> (donnée du manifeste) sans aucune
/// validation — un manifeste compromis pouvait donc faire écrire N'IMPORTE
/// OÙ sur le disque. Voir <c>Security/PathSafetyTests.cs</c> et
/// <c>Adversarial/PathTraversalTests.cs</c> pour la reproduction.
/// </summary>
public static class PathSafety
{
    private static readonly HashSet<string> ReservedWindowsNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    };

    // Caractères interdits dans un nom de fichier Windows (surensemble de
    // ceux interdits sous Linux) — le moteur doit rester sûr sur les DEUX
    // plateformes puisqu'il est développé/testé sous Linux mais déployé
    // sous Windows (voir architecture Setup verrouillée).
    private static readonly char[] InvalidFileNameChars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*'];

    /// <summary>
    /// True si <paramref name="candidate"/> est un nom de fichier SIMPLE
    /// (jamais un chemin — aucun séparateur), sans traversée, sans nom
    /// réservé Windows, sans caractère interdit. Un ArtifactId de
    /// manifeste ne doit jamais être autre chose qu'un nom de fichier.
    /// </summary>
    public static bool IsSafeFileName(string candidate, out string reason)
    {
        if (string.IsNullOrWhiteSpace(candidate))
        {
            reason = "vide";
            return false;
        }
        if (candidate.Contains('\0'))
        {
            reason = "caractère nul";
            return false;
        }
        if (candidate is "." or "..")
        {
            reason = "segment de traversée";
            return false;
        }
        if (candidate.StartsWith(@"\\", StringComparison.Ordinal) || candidate.StartsWith("//", StringComparison.Ordinal))
        {
            reason = "chemin UNC";
            return false;
        }
        if (Path.IsPathRooted(candidate))
        {
            reason = "chemin absolu";
            return false;
        }
        if (candidate.IndexOfAny(['/', '\\']) >= 0)
        {
            reason = "doit être un nom de fichier simple, jamais un chemin composé";
            return false;
        }
        foreach (var c in InvalidFileNameChars)
        {
            if (candidate.Contains(c))
            {
                reason = $"caractère interdit : '{c}'";
                return false;
            }
        }
        var stem = candidate.Split('.')[0];
        if (ReservedWindowsNames.Contains(stem))
        {
            reason = $"nom réservé Windows : {stem}";
            return false;
        }
        if (candidate.EndsWith(' ') || candidate.EndsWith('.'))
        {
            reason = "se termine par un espace ou un point (invalide sous Windows)";
            return false;
        }
        reason = string.Empty;
        return true;
    }

    /// <summary>
    /// Combine <paramref name="baseDirectory"/> et <paramref name="fileNameSegment"/>
    /// en vérifiant à la fois que le segment est un nom de fichier sûr ET
    /// que le résultat final reste réellement À L'INTÉRIEUR de
    /// <paramref name="baseDirectory"/> (défense en profondeur — même si
    /// <see cref="IsSafeFileName"/> bloque déjà tout séparateur, une
    /// vérification a posteriori sur le chemin résolu protège contre une
    /// régression future de cette règle). Lève <see cref="UnsafePathException"/>
    /// plutôt que de corriger silencieusement.
    /// </summary>
    public static string SafeCombine(string baseDirectory, string fileNameSegment)
    {
        if (!IsSafeFileName(fileNameSegment, out var reason))
        {
            throw new UnsafePathException($"segment de chemin refusé (« {fileNameSegment} ») : {reason}");
        }

        var baseFull = Path.GetFullPath(baseDirectory);
        var combined = Path.GetFullPath(Path.Combine(baseFull, fileNameSegment));
        var baseWithSeparator = baseFull.EndsWith(Path.DirectorySeparatorChar) ? baseFull : baseFull + Path.DirectorySeparatorChar;
        if (!combined.StartsWith(baseWithSeparator, StringComparison.Ordinal))
        {
            throw new UnsafePathException($"le chemin résultant « {combined} » sort de « {baseFull} »");
        }
        return combined;
    }
}
