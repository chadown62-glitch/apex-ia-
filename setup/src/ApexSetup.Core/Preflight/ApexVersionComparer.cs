namespace ApexSetup.Core.Preflight;

/// <summary>
/// Comparaison de versions APEX (<c>"0.1.2"</c>, format
/// <c>apex.version.__version__</c> côté Runtime — jamais garanti SemVer
/// complet). Tolérante à un nombre de segments différent, jamais une
/// levée d'exception sur une chaîne inattendue — un format imprévu doit
/// se traduire par « incompatible », jamais planter le Preflight.
/// </summary>
public static class ApexVersionComparer
{
    public static bool TryParse(string version, out IReadOnlyList<int> segments)
    {
        var parts = version.Split('.', StringSplitOptions.RemoveEmptyEntries);
        var parsed = new List<int>(parts.Length);
        foreach (var part in parts)
        {
            if (!int.TryParse(part, out var n))
            {
                segments = [];
                return false;
            }
            parsed.Add(n);
        }
        segments = parsed;
        return parsed.Count > 0;
    }

    /// <summary>True si <paramref name="observed"/> &gt;= <paramref name="minimum"/>, comparaison segment par segment.</summary>
    public static bool IsAtLeast(string observed, string minimum)
    {
        if (!TryParse(observed, out var observedSegments) || !TryParse(minimum, out var minimumSegments))
        {
            return false;
        }

        var length = Math.Max(observedSegments.Count, minimumSegments.Count);
        for (var i = 0; i < length; i++)
        {
            var a = i < observedSegments.Count ? observedSegments[i] : 0;
            var b = i < minimumSegments.Count ? minimumSegments[i] : 0;
            if (a != b)
            {
                return a > b;
            }
        }
        return true; // égales
    }
}
