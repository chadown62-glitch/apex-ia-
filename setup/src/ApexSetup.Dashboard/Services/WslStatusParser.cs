namespace ApexSetup.Dashboard.Services;

public sealed record WslDistroInfo(string Name, bool IsDefault, bool IsRunning);

public sealed record WslProbeResult(bool WslInstalled, IReadOnlyList<WslDistroInfo> Distros, string Detail)
{
    public static WslProbeResult NotInstalled(string detail) => new(false, [], detail);
}

/// <summary>
/// Parseur PUR (aucune E/S) de la sortie de <c>wsl.exe -l -v</c> — séparé de
/// <see cref="WindowsWslProbe"/> précisément pour rester testable sans
/// Windows ni sous-processus (mission §20 : « détection WSL » doit être
/// testable). Format réel de `wsl.exe -l -v` (observé, documenté par
/// Microsoft) :
/// <code>
///   NAME      STATE           VERSION
/// * Ubuntu    Running         2
///   Debian    Stopped         2
/// </code>
/// la distribution par défaut est marquée d'un <c>*</c> en première colonne.
/// </summary>
public static class WslStatusParser
{
    public static IReadOnlyList<WslDistroInfo> Parse(string rawOutput)
    {
        // `wsl.exe` émet historiquement de l'UTF-16LE même quand stdout est
        // redirigé vers un pipe .NET qui s'attend à de l'UTF-8/ASCII — sans
        // ce nettoyage, chaque caractère apparaît séparé par un octet nul
        // et aucune ligne ne correspond au motif attendu. Nettoyage
        // défensif, jamais une supposition qu'il n'arrivera pas.
        var cleaned = rawOutput.Replace("\0", string.Empty);
        var lines = cleaned.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

        var distros = new List<WslDistroInfo>();
        foreach (var line in lines)
        {
            if (line.Length == 0 || line.StartsWith("NAME", StringComparison.OrdinalIgnoreCase))
            {
                continue; // ligne d'en-tête ou vide
            }

            var isDefault = line.StartsWith('*');
            var content = isDefault ? line[1..].Trim() : line;
            var parts = content.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2)
            {
                continue; // ligne inattendue — jamais une supposition, simplement ignorée
            }

            var name = parts[0];
            var state = parts[1];
            distros.Add(new WslDistroInfo(name, isDefault, state.Equals("Running", StringComparison.OrdinalIgnoreCase)));
        }

        return distros;
    }
}
