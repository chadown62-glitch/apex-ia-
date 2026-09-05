using ApexSetup.Application.Contracts;
using ApexSetup.Application.Requests;

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
}
