using ApexSetup.Core.Contracts;

namespace ApexSetup.Core.Hardware;

/// <summary>
/// Sélectionne un profil RECOMMANDÉ à partir du matériel détecté —
/// RECOMMANDATION UNIQUEMENT. N'autorise jamais rien : seul le Gate
/// (<c>apex gate --json</c>, mesuré, non modifié par ce moteur) décide de
/// ce qui est réellement supporté. Voir <see cref="HardwareProfileCatalog"/>
/// pour la traçabilité des valeurs vers <c>config.yaml</c>.
/// </summary>
public static class HardwareProfileEngine
{
    /// <summary>
    /// Sélectionne le plus haut profil dont les seuils VRAM/RAM/CPU sont
    /// satisfaits par le matériel détecté — jamais le contraire (ne
    /// jamais recommander un profil que le matériel ne peut pas
    /// atteindre). Le disque n'entre pas dans le choix du profil ici (il
    /// est vérifié séparément à l'étape « Disk space check » du pipeline
    /// d'installation) — deux préoccupations indépendantes.
    /// </summary>
    public static HardwareProfile SelectRecommendedProfile(DetectedHardware detected)
    {
        HardwareProfile? best = null;
        foreach (var tier in new[]
                 {
                     HardwareProfileTier.HighEnd, HardwareProfileTier.Performance,
                     HardwareProfileTier.Standard, HardwareProfileTier.Minimal,
                 })
        {
            var candidate = HardwareProfileCatalog.Profiles[tier];
            if (detected.VramTotalGb >= candidate.RecommendedVramGb
                && detected.RamTotalGb >= candidate.RecommendedRamGb
                && detected.CpuThreads >= candidate.RecommendedCpuThreads)
            {
                best = candidate;
                break;
            }
        }

        // Matériel sous le seuil MINIMAL lui-même : toujours retourner
        // MINIMAL plutôt que de lever une exception — c'est au Gate
        // (jamais à ce moteur) de refuser l'installation si le matériel
        // est réellement insuffisant. Une recommandation n'est pas un
        // refus.
        return best ?? HardwareProfileCatalog.Profiles[HardwareProfileTier.Minimal];
    }
}
