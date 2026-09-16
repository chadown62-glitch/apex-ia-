using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

public enum OrchestratorOutcome
{
    Success,
    ProjectNotConfigured,
    WslUnavailable,
    ProjectPathNotFound,
    BackendFailedToStart,
    BackendCrashed,
    TimedOutWaitingForApi,
    TimedOutWaitingForWebPanel,
    StopFailed,
    RepairFailed,

    /// <summary>
    /// Un processus backend suivi par CE Dashboard tourne déjà — StartAsync
    /// refuse d'en lancer un second (finalisation APEX.exe 1/2, défaut réel
    /// démontré : rien n'empêchait auparavant un second appel à StartAsync,
    /// après un premier succès, de lancer un second wsl.exe et d'écraser la
    /// référence au premier processus suivi — un vrai processus zombie,
    /// exactement le scénario « double lancement » nommé par la mission).
    /// </summary>
    AlreadyRunning,
}

/// <summary>
/// Orchestration réelle de démarrage/arrêt/redémarrage/réparation — mission
/// §5/§13/§14/§15. Chaque étape du démarrage est une VÉRIFICATION RÉELLE
/// bornée dans le temps, jamais un délai fixe suivi d'une supposition
/// (« attendre 10 secondes -> considérer que c'est OK » est explicitement
/// interdit par la mission).
/// </summary>
public interface IApexOrchestrator
{
    /// <summary>Un processus suivi par CE Dashboard existe et n'a pas quitté — jamais déduit d'une recherche par nom de processus sur la machine.</summary>
    bool IsBackendTracked { get; }

    bool IsFrontendTracked { get; }

    Task<OrchestratorOutcome> StartAsync(DashboardSettings settings, CancellationToken cancellationToken = default);

    /// <summary>N'arrête QUE ce que ce Dashboard a lui-même démarré (processus suivis) + l'infrastructure Docker via <c>scripts/apex-launch.sh stop</c> (qui applique la même garantie côté WSL — jamais un service externe touché). Voir mission §13.</summary>
    Task<OrchestratorOutcome> StopAsync(DashboardSettings settings, CancellationToken cancellationToken = default);

    Task<OrchestratorOutcome> RestartAsync(DashboardSettings settings, CancellationToken cancellationToken = default);

    /// <summary>Relance <c>apex start --dry-run</c> (auto-réparation SAFE déjà prévue côté Runtime, <c>apex.doctor.repair</c>) — jamais une nouvelle logique de réparation inventée côté Dashboard. Voir mission §15.</summary>
    Task<OrchestratorOutcome> RepairAsync(DashboardSettings settings, CancellationToken cancellationToken = default);
}
