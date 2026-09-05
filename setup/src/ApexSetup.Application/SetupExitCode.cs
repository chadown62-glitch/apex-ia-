namespace ApexSetup.Application;

/// <summary>
/// Codes de sortie DÉTERMINISTES du CLI Setup — une catégorie de résultat
/// correspond TOUJOURS au même entier, quelle que soit la commande. Un
/// futur GUI (ou un script d'orchestration) peut donc décider sur le seul
/// code de sortie, sans jamais avoir besoin de parser un message humain.
///
/// Au-delà du minimum exigé (succès ; erreur de paramètres ; preflight
/// bloquant ; Runtime indisponible ; Gate bloquant ; téléchargement
/// échoué ; intégrité échouée ; installation échouée ; rollback échoué ;
/// recovery requis ; validation échouée ; erreur interne inattendue), DEUX
/// codes supplémentaires ont été ajoutés — une extension, jamais une
/// modification du minimum demandé :
/// <see cref="ConcurrencyRefused"/> (une installation concurrente
/// existante refuse explicitement une seconde exécution — voir
/// <c>InstallationLock</c>) et <see cref="InconsistentState"/> (état
/// transactionnel corrompu détecté par <c>RecoveryClassifier</c>,
/// nécessitant une intervention humaine — distinct d'un
/// <see cref="RecoveryRequired"/> qui, lui, se répare automatiquement via
/// <c>setup recover</c>).
/// </summary>
public enum SetupExitCode
{
    Success = 0,
    InvalidArguments = 1,
    PreflightBlocking = 2,
    RuntimeUnavailable = 3,
    GateBlocking = 4,
    DownloadFailed = 5,
    IntegrityFailed = 6,
    InstallationFailed = 7,
    RollbackFailed = 8,
    RecoveryRequired = 9,
    ValidationFailed = 10,
    ConcurrencyRefused = 11,
    InconsistentState = 12,

    /// <summary>Toujours 99 — délibérément loin des autres pour ne jamais être confondu avec une extension future du minimum ci-dessus.</summary>
    InternalError = 99,
}
