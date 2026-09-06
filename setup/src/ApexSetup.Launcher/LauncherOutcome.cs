namespace ApexSetup.Launcher;

/// <summary>
/// Les 8 états de lancement exigés par la mission N+15 (§ Phase E,
/// « Détection et auto-réparation ») — PROJECTION des contrats Setup déjà
/// existants (<c>SetupLifecycleState</c>, <c>GateSummary</c>,
/// <c>DoctorSummary</c>, <c>ApexVersionComparer</c>), jamais un nouvel état
/// métier : <see cref="LauncherStateEngine"/> ne fait que lire ces
/// contrats, il ne les réinterprète ni ne les redéfinit. Aucun état
/// supplémentaire n'est ajouté au-delà des 8 requis.
/// </summary>
public enum LauncherOutcome
{
    /// <summary>APEX est installé, le Runtime répond, Gate autorise, version à jour — démarrage direct possible.</summary>
    Ready,

    /// <summary>Aucune installation détectée (<c>SetupLifecycleState.NoInstallation</c>, ou rollback venant de la ramener à cet état) — le Setup doit tourner avant tout démarrage.</summary>
    NeedsSetup,

    /// <summary>Installation interrompue, réparable automatiquement par <c>ISetupApplicationService.RecoverAsync</c> (<c>SetupLifecycleState.RecoveryNeeded</c>).</summary>
    NeedsRecovery,

    /// <summary>Installation et Runtime valides, mais version observée strictement antérieure au plancher requis (<c>ApexVersionComparer.IsAtLeast</c>).</summary>
    NeedsUpdate,

    /// <summary>Gate refuse explicitement l'autorisation (<c>GateSummary.Phase2Authorized == false</c> ou blocages non résolus) — jamais contourné.</summary>
    Blocked,

    /// <summary>État transactionnel incohérent, non auto-réparable (<c>SetupLifecycleState.Inconsistent</c>) — nécessite une intervention humaine via le GUI Setup.</summary>
    InvalidInstall,

    /// <summary>Installation valide mais le Runtime ne répond pas ou le Doctor rapporte un échec bloquant (<c>ValidateResponse.RuntimeReachable == false</c> ou <c>DoctorSummary.Status == "fail"</c>).</summary>
    RuntimeError,

    /// <summary>Toute situation non couverte par les 7 états ci-dessus (réponse manquante, erreurs structurées inattendues, état de cycle de vie non reconnu) — jamais un crash silencieux du launcher lui-même.</summary>
    UnknownError,
}
