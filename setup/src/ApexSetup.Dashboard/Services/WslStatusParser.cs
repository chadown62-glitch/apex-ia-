namespace ApexSetup.Dashboard.Services;

public sealed record WslDistroInfo(string Name, bool IsDefault, bool IsRunning);

/// <summary>
/// Parseur PUR (aucune E/S) des sorties de <c>wsl.exe</c>, INDÉPENDANT DE LA LANGUE de Windows.
/// <c>wsl.exe -l -v</c> est traduit (en-tête, colonne d'état — un état traduit peut contenir des
/// espaces) : on n'en retient que ce qui ne l'est pas, le marqueur <c>*</c> de la distribution par
/// défaut et le NOM (première colonne, sans espace). L'état « en cours d'exécution » vient de
/// <c>wsl.exe -l --running -q</c>, qui n'imprime que des noms ; un message éventuel (« aucune
/// distribution… », traduit lui aussi) n'est jamais pris pour un nom, car seuls les noms déjà connus
/// par <c>-l -v</c> sont retenus. <c>wsl.exe</c> écrit de l'UTF-16 : les octets nuls sont retirés.
/// </summary>
public static class WslStatusParser
{
    public static IReadOnlyList<WslDistroInfo> Parse(string listVerbose, string? runningQuiet = null)
    {
        var lines = Lines(listVerbose);
        var running = runningQuiet is null ? null : Lines(runningQuiet).Select(l => l.Trim()).ToHashSet(StringComparer.OrdinalIgnoreCase);

        var distros = new List<WslDistroInfo>();
        // La première ligne est toujours l'en-tête, quelle que soit la langue (NAME/NOM/…).
        foreach (var line in lines.Skip(1))
        {
            var isDefault = line.StartsWith('*');
            var content = isDefault ? line[1..].Trim() : line;
            var parts = content.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2)
            {
                continue; // ligne inattendue : ignorée, jamais une supposition
            }
            var name = parts[0];
            // Sans la liste des distributions en cours (ancien appel), repli sur le mot anglais « Running ».
            var isRunning = running?.Contains(name) ?? parts[1].Equals("Running", StringComparison.OrdinalIgnoreCase);
            distros.Add(new WslDistroInfo(name, isDefault, isRunning));
        }
        return distros;
    }

    private static List<string> Lines(string raw) =>
        raw.Replace("\0", string.Empty)
            .Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(l => l.Length > 0)
            .ToList();
}
