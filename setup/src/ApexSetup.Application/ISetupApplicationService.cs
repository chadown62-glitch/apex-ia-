using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;
using ApexSetup.Core.Contracts;
using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Application;

/// <summary>
/// LA frontière GUI/Core exigée par la spécification Phase 3 (§14) : un
/// futur GUI appelle EXACTEMENT ces 8 méthodes — jamais
/// <c>DownloadStage</c>, <c>InstallationStage</c>, <c>InstallationPipeline</c>,
/// <c>PathSafety</c>, etc. directement. Le CLI (<c>ApexSetup.Cli</c>) et un
/// futur GUI sont deux FAÇADES INTERCHANGEABLES au-dessus de la même
/// implémentation (<see cref="SetupApplicationService"/>) — remplacer le
/// CLI par un GUI ne nécessite de modifier NI cette interface NI aucun
/// fichier de <c>ApexSetup.Core</c>.
/// </summary>
public interface ISetupApplicationService
{
    Task<VersionResponse> GetVersionAsync(CancellationToken cancellationToken = default);

    Task<DetectResponse> DetectAsync(string destinationForDiskProbe, CancellationToken cancellationToken = default);

    /// <summary>Preflight complet. Ne déclenche JAMAIS d'installation.</summary>
    Task<CheckResponse> CheckAsync(SetupPlanRequest request, CancellationToken cancellationToken = default);

    /// <summary>Construit et retourne le plan. Ne modifie JAMAIS la machine.</summary>
    Task<PlanResponse> PlanAsync(SetupPlanRequest request, CancellationToken cancellationToken = default);

    /// <summary>Orchestre le pipeline complet. Ne déclare jamais SUCCESS avant Commit (garanti par <c>InstallationPipeline</c> lui-même).</summary>
    Task<InstallResponse> InstallAsync(SetupInstallRequest request, CancellationToken cancellationToken = default);

    /// <summary>Classifie puis, seulement si sûr de le faire, répare une installation interrompue. Ne fait jamais de recovery silencieuse.</summary>
    Task<RecoverResponse> RecoverAsync(string destination, CancellationToken cancellationToken = default);

    /// <summary>Validation post-installation SANS réinstaller — RuntimeAdapter uniquement.</summary>
    Task<ValidateResponse> ValidateAsync(CancellationToken cancellationToken = default);

    /// <summary>Lecture pure de l'état transactionnel courant — aucun effet secondaire durable.</summary>
    Task<StatusResponse> StatusAsync(string destination, CancellationToken cancellationToken = default);

    /// <summary>
    /// Détail COMPLET d'<c>apex doctor --json</c> (tous les <c>results[]</c>,
    /// pas seulement le résumé agrégé que <see cref="ValidateAsync"/>
    /// expose via <see cref="Contracts.DoctorSummary"/>). Ajouté pour
    /// qu'un futur consommateur (tableau de bord par composant : WSL,
    /// Docker, PostgreSQL, Redis, MinIO, Ollama, GPU) puisse lire l'état
    /// RÉEL de chaque check individuel sans qu'aucun appelant ne
    /// réinterprète ou ne recalcule le diagnostic — un pur passe-plat vers
    /// <c>RuntimeAdapter.GetDoctorAsync</c>, déjà appelé par
    /// <see cref="ValidateAsync"/> pour construire son résumé. Aucune
    /// nouvelle logique de diagnostic : la même mesure, simplement non
    /// tronquée.
    /// </summary>
    Task<RuntimeCallResult<RuntimeDoctorResult>> GetDoctorDetailAsync(CancellationToken cancellationToken = default);

    /// <summary>Symétrique de <see cref="GetDoctorDetailAsync"/> pour <c>apex gate --json</c> — le détail complet des <c>checks[]</c>, jamais recalculé.</summary>
    Task<RuntimeCallResult<RuntimeGateResult>> GetGateDetailAsync(CancellationToken cancellationToken = default);
}
