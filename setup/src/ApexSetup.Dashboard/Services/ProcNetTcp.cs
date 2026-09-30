using System.Globalization;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Parseur PUR de <c>/proc/net/tcp</c> et <c>/proc/net/tcp6</c> (format du noyau Linux, jamais traduit) :
/// une socket en écoute (état <c>0A</c>) sur un port donné existe-t-elle dans la distribution ? Sert à
/// distinguer « backend arrêté » de « backend qui écoute dans WSL mais que Windows n'atteint pas par
/// localhost » (défaut W2 de l'audit). Lecture d'un fichier par <c>cat</c>, aucun shell, aucun outil
/// supplémentaire requis dans la distribution.
/// </summary>
public static class ProcNetTcp
{
    private const string ListenState = "0A";

    /// <summary>
    /// Vrai si une ligne décrit une socket en écoute sur <paramref name="port"/> ; lignes illisibles
    /// ignorées (jamais une supposition).
    /// </summary>
    public static bool IsListening(string? content, int port)
    {
        if (string.IsNullOrEmpty(content))
        {
            return false;
        }
        foreach (var line in content.Split('\n').Skip(1))
        {
            // « sl local_address rem_address st … » ; local_address = <adresse hex>:<port hex>
            var fields = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (fields.Length < 4 || fields[3] != ListenState)
            {
                continue;
            }
            var colon = fields[1].LastIndexOf(':');
            if (colon > 0
                && int.TryParse(fields[1].AsSpan(colon + 1), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var listening)
                && listening == port)
            {
                return true;
            }
        }
        return false;
    }
}
