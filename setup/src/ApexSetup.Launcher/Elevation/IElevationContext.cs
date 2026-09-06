namespace ApexSetup.Launcher.Elevation;

/// <summary>
/// Frontière testable autour de la détection de privilèges Windows —
/// jamais appelée directement par <c>Program</c>, qui ne connaît que cette
/// interface (même patron que <c>IProcessLauncher</c>). Permet de tester
/// la logique de décision d'élévation (« faut-il UAC ? ») avec un faux,
/// sans dépendre de l'API Windows réelle, indisponible dans ce bac à
/// sable Linux.
/// </summary>
public interface IElevationContext
{
    /// <summary>Vrai si le processus courant tourne déjà avec un jeton administrateur (Windows) — jamais élevé une seconde fois si déjà vrai (règle « pas de boucle UAC »).</summary>
    bool IsElevated { get; }

    /// <summary>
    /// Vrai si l'action demandée (ex. installation, réparation touchant
    /// des chemins protégés) nécessite réellement des privilèges élevés
    /// sur CETTE machine — jamais une supposition statique : dépend de
    /// l'accessibilité en écriture du répertoire d'installation cible.
    /// </summary>
    bool RequiresElevationFor(string targetPath);
}
