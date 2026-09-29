namespace ApexSetup.Dashboard.Models;

/// <summary>Verdict global affiché en tête du Panel.</summary>
public enum PanelVerdict
{
    /// <summary>Tous les composants sont READY.</summary>
    Ready,

    /// <summary>L'API répond, mais au moins un service n'est pas READY.</summary>
    Degraded,

    /// <summary>APEX n'est pas utilisable ; <see cref="NotReadyCause"/> dit pourquoi.</summary>
    NotReady,

    /// <summary>Panel et backend ne parlent pas le même protocole : aucun état n'est interprété.</summary>
    VersionMismatch,
}

/// <summary>Cause précise d'un verdict non READY — une par situation distincte que l'utilisateur doit corriger différemment.</summary>
public enum NotReadyCause
{
    None,
    WslNotInstalled,
    WslStopped,
    WslUnresponsive,
    BackendNotStarted,
    BackendStarting,
    BackendStopped,
    BackendUnresponsive,
    ApiTimeout,
    ApiError,
    TokenMissing,
    TokenRejected,
    PortConflict,
    DiscoveryInvalid,
    PartialServices,
    BackendTooOld,
    PanelTooOld,
}

/// <summary>Versions constatées (jamais supposées) : celle du Panel est la sienne ; celles du backend viennent de sa réponse.</summary>
public sealed record VersionInfo(
    string PanelVersion,
    int PanelProtocol,
    string? BackendApexVersion,
    string? BackendApiVersion,
    int? BackendProtocol);

/// <summary>Informations de découverte affichées dans le diagnostic (aucun secret).</summary>
public sealed record BackendInfo(int? Port, int? Pid, string? ProjectRoot, DateTimeOffset? StartedAt, string? Host);

/// <summary>Une mesure complète du Panel, à un instant.</summary>
public sealed record PanelSnapshot(
    DateTimeOffset MeasuredAt,
    IReadOnlyList<ComponentState> Components,
    PanelVerdict Verdict,
    NotReadyCause Cause,
    string Headline,
    string Guidance,
    VersionInfo Versions,
    BackendInfo Backend,
    bool WslApplicable,
    string? WslDistro,
    TimeSpan Duration)
{
    public ComponentState? Get(ComponentKey key) => Components.FirstOrDefault(c => c.Key == key);
}
