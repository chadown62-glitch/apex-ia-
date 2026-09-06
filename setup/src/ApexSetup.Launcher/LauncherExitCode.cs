namespace ApexSetup.Launcher;

/// <summary>
/// Codes de sortie DÉTERMINISTES du launcher — contrat SÉPARÉ de
/// <c>ApexSetup.Application.SetupExitCode</c> (jamais touché : c'est le
/// contrat du CLI Setup, pas celui du launcher, voir règle absolue #4/#5
/// « réutiliser au maximum, ne jamais casser les contrats existants »).
/// Les valeurs qui partagent une signification avec <c>SetupExitCode</c>
/// réutilisent volontairement le MÊME entier (3, 4, 9, 12, 99) — jamais
/// une coïncidence, documentée ci-dessous à chaque cas — tandis que les
/// concepts propres au launcher (pas d'installation du tout, élévation
/// refusée) reçoivent des valeurs qui n'entrent en collision avec AUCUNE
/// des deux échelles.
/// </summary>
public enum LauncherExitCode
{
    Success = 0,
    InvalidArguments = 1,

    /// <summary>Même valeur que <c>SetupExitCode.RuntimeUnavailable</c> — même signification exacte (le Runtime ne répond pas).</summary>
    RuntimeError = 3,

    /// <summary>Même valeur que <c>SetupExitCode.GateBlocking</c> — Gate a refusé, jamais contourné.</summary>
    Blocked = 4,

    /// <summary>Même valeur que <c>SetupExitCode.RecoveryRequired</c> — une réparation existe mais n'a pas été effectuée (déclinée par l'utilisateur ou échouée).</summary>
    RecoveryRequired = 9,

    /// <summary>Même valeur que <c>SetupExitCode.InconsistentState</c>.</summary>
    InvalidInstall = 12,

    /// <summary>Concept propre au launcher (sans équivalent dans <c>SetupExitCode</c>, qui suppose toujours une installation déjà commencée) — volontairement loin de la plage 0-12 pour ne jamais se confondre avec elle.</summary>
    NeedsSetup = 20,

    /// <summary>L'utilisateur a refusé l'invite UAC — concept propre au launcher.</summary>
    ElevationDeclined = 21,

    /// <summary>Version installée trop ancienne — concept propre au launcher (Setup seul n'a pas la notion de plancher de version applicative).</summary>
    NeedsUpdate = 22,

    UnknownError = 98,

    /// <summary>Même valeur que <c>SetupExitCode.InternalError</c> — volontairement loin de tout le reste, comme dans le contrat Setup.</summary>
    InternalError = 99,
}
