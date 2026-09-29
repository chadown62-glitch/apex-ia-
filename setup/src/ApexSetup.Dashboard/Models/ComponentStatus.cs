namespace ApexSetup.Dashboard.Models;

/// <summary>
/// Vocabulaire EXACT du Panel — six valeurs, jamais une septième. <see cref="Ready"/> n'est attribué
/// que sur une mesure réelle positive (une réponse HTTP authentifiée du backend, ou l'état qu'IL a
/// mesuré pour un service) — jamais sur une configuration, un fichier présent ou un processus lancé.
/// </summary>
public enum ComponentStatus
{
    /// <summary>Mesure réelle positive.</summary>
    Ready,

    /// <summary>Démarrage observé (enregistrement de découverte récent, processus vivant) mais pas encore de réponse.</summary>
    Starting,

    /// <summary>Mesure réelle d'absence : arrêté, non démarré, connexion refusée, non configuré.</summary>
    Offline,

    /// <summary>Répond, mais incomplet (ex. Ollama sans modèle, backend qui renvoie une erreur).</summary>
    Degraded,

    /// <summary>Répond mal, ou mesure impossible pour une raison anormale (jeton refusé, délai dépassé, autre programme sur le port).</summary>
    Error,

    /// <summary>Non mesuré (ex. le backend est injoignable : les services derrière lui ne sont pas mesurables d'ici).</summary>
    Unknown,
}
