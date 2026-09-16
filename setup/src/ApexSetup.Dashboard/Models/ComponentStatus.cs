namespace ApexSetup.Dashboard.Models;

/// <summary>
/// Vocabulaire EXACT exigé par la mission (§4) pour le tableau « System
/// Status » — sept valeurs, jamais une huitième inventée ni une valeur
/// devinée. <see cref="Running"/> n'est JAMAIS attribué simplement parce
/// qu'une commande de démarrage a été envoyée : chaque valeur ici est
/// dérivée d'une mesure réelle (un check <c>apex doctor</c>/<c>apex
/// gate</c> déjà exécuté, ou une requête HTTP réellement effectuée) —
/// voir <see cref="Services.IRuntimeStatusService"/>/<see cref="Services.IApiHealthChecker"/>,
/// jamais une supposition optimiste après l'envoi d'une commande.
/// </summary>
public enum ComponentStatus
{
    /// <summary>Mesure réelle confirmant un fonctionnement correct.</summary>
    Running,

    /// <summary>Une action de démarrage est en cours ; aucune confirmation réelle de disponibilité encore obtenue.</summary>
    Starting,

    /// <summary>Mesure réelle confirmant l'arrêt (ex. conteneur absent, port fermé).</summary>
    Stopped,

    /// <summary>Mesure réelle d'un échec (ex. check Doctor en FAIL, HTTP 500, connexion refusée).</summary>
    Error,

    /// <summary>Le composant n'est pas installé sur cette machine (ex. Docker absent du PATH).</summary>
    NotInstalled,

    /// <summary>Le composant est installé mais ne peut pas être mesuré dans les conditions actuelles (ex. GPU absent — n'est jamais une erreur en soi sur une machine CPU-only).</summary>
    Unavailable,

    /// <summary>Aucune mesure n'a encore pu être obtenue (ex. Runtime injoignable) — jamais interprété comme un état positif ou négatif par défaut.</summary>
    Unknown,
}
