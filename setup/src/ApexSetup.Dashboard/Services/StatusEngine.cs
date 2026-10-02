using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>Tout ce qui a été MESURÉ pendant une vérification — l'entrée de <see cref="StatusEngine.Decide"/>.</summary>
public sealed record StatusInputs(
    HostProbe Host,
    DiscoveryRead Discovery,
    bool? BackendProcessAlive,
    TokenRead Token,
    StatusFetch Fetch,
    int Port,
    DateTimeOffset Now,
    TimeSpan Duration,
    bool? ListeningInHost = null,
    LaunchAttempt? Launch = null);

/// <summary>
/// Fonction PURE (aucune E/S) : mesures -> états, verdict et consigne. C'est ici que se décide ce
/// que le Panel affirme ; règles :
/// <list type="bullet">
/// <item>READY seulement sur une réponse authentifiée et au bon protocole ;</item>
/// <item>un service derrière un backend injoignable est UNKNOWN, jamais deviné ;</item>
/// <item>un enregistrement de découverte ne prouve rien : il ne distingue que « en démarrage » / « arrêté » ;</item>
/// <item>un protocole différent n'est jamais interprété (VERSION_MISMATCH).</item>
/// </list>
/// </summary>
public static class StatusEngine
{
    /// <summary>Protocole Panel/backend pris en charge (<c>PANEL_PROTOCOL</c> dans <c>apex/version.py</c>).</summary>
    public const int SupportedProtocol = 1;

    /// <summary>Délai pendant lequel un backend enregistré mais muet est considéré « en démarrage ».</summary>
    public static readonly TimeSpan StartupGrace = TimeSpan.FromSeconds(120);

    public const string LaunchCommand = "bash scripts/apex-launch.sh serve";

    /// <summary>Le geste normal, sans terminal : le bouton du Panel (même chemin que launch.sh, Startup Gate compris).</summary>
    public const string StartButton = "« Démarrer APEX »";

    public static PanelSnapshot Decide(StatusInputs m, string panelVersion)
    {
        var rows = new List<ComponentState>();
        var where = m.Host.Applicable ? "dans WSL" : "dans un terminal";
        if (m.Host.Applicable)
        {
            rows.Add(new ComponentState(ComponentKey.Wsl, m.Host.Status, m.Host.Detail));
        }
        var record = m.Discovery.Record;
        var versions = new VersionInfo(panelVersion, SupportedProtocol, record?.ApexVersion, null, record?.PanelProtocol);
        var backendInfo = new BackendInfo(m.Port, record?.Pid, record?.ProjectRoot, record?.StartedAt, record?.Host);

        PanelSnapshot Snapshot(PanelVerdict verdict, NotReadyCause cause, string guidance, VersionInfo? v = null) =>
            new(m.Now, rows, verdict, cause, Headline(verdict), guidance, v ?? versions, backendInfo,
                m.Host.Applicable, m.Host.Distro, m.Duration);

        void Services(ComponentStatus status, string detail)
        {
            foreach (var (key, _) in ComponentKeys.BackendServices)
            {
                rows.Add(new ComponentState(key, status, detail));
            }
        }

        // 1. L'hôte ne tourne pas : rien n'est lu, rien n'est démarré (sauf demande explicite : « Démarrer APEX »).
        if (m.Host.Applicable && !m.Host.Running && m.Launch is { Running: true } pending && m.Now - pending.StartedAt < StartupGrace)
        {
            rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Starting, "démarrage demandé : WSL démarre"));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Unknown, "non mesurée : démarrage en cours"));
            Services(ComponentStatus.Unknown, "non mesuré : démarrage en cours");
            return Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendStarting,
                "Démarrage d'APEX demandé : la distribution Linux démarre, puis launch.sh prépare APEX (Docker, doctor, Startup Gate). Le Panel se met à jour automatiquement.");
        }
        if (m.Host.Applicable && !m.Host.Running)
        {
            rows.Add(new ComponentState(ComponentKey.Backend, m.Host.Status == ComponentStatus.Offline ? ComponentStatus.Offline : ComponentStatus.Unknown,
                m.Host.Status == ComponentStatus.Offline ? "WSL est arrêté : le backend ne peut pas tourner" : "non mesuré : WSL indisponible"));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Unknown, "non mesurée : WSL indisponible"));
            Services(ComponentStatus.Unknown, "non mesuré : WSL indisponible");
            var cause = m.Host.Cause == NotReadyCause.None ? NotReadyCause.WslUnresponsive : m.Host.Cause;
            return Snapshot(PanelVerdict.NotReady, cause, cause switch
            {
                NotReadyCause.WslStopped =>
                    "WSL n'est pas démarré. Cliquez sur " + StartButton + " : la distribution Linux démarre, puis APEX (même chemin que launch.sh, Startup Gate compris). "
                    + "Le Panel ne démarre jamais WSL sans ce clic.",
                NotReadyCause.WslNotInstalled =>
                    m.Host.Detail + ". Installez WSL 2 et une distribution Linux (documentation Microsoft : « wsl --install »), puis le backend APEX dans cette distribution.",
                _ => "WSL ne répond pas (" + m.Host.Detail + "). Redémarrez WSL (wsl --shutdown puis rouvrez la distribution).",
            });
        }

        // 2. Le backend a répondu avec une réponse APEX valide.
        if (m.Fetch.Outcome == FetchOutcome.Ok && m.Fetch.Payload is { } payload)
        {
            versions = versions with
            {
                BackendApexVersion = payload.ApexVersion,
                BackendApiVersion = payload.ApiVersion,
                BackendProtocol = payload.PanelProtocol,
            };
            backendInfo = backendInfo with { Pid = payload.Pid ?? backendInfo.Pid };
            if (payload.PanelProtocol != SupportedProtocol)
            {
                rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Ready, $"APEX {payload.ApexVersion} répond"));
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error,
                    $"protocole {payload.PanelProtocol} non pris en charge (Panel : {SupportedProtocol})"));
                Services(ComponentStatus.Unknown, "non interprété : protocole incompatible");
                return payload.PanelProtocol > SupportedProtocol
                    ? Snapshot(PanelVerdict.VersionMismatch, NotReadyCause.PanelTooOld,
                        $"Ce Panel (protocole {SupportedProtocol}) est plus ancien que le backend APEX {payload.ApexVersion} (protocole {payload.PanelProtocol}). Installez la dernière version d'APEX.exe.")
                    : Snapshot(PanelVerdict.VersionMismatch, NotReadyCause.BackendTooOld,
                        $"Le backend APEX {payload.ApexVersion} (protocole {payload.PanelProtocol}) est plus ancien que ce Panel (protocole {SupportedProtocol}). Mettez à jour le dépôt APEX {where} puis relancez {LaunchCommand}.");
            }

            var age = record is null ? "" : $" · démarré {Ago(m.Now - record.StartedAt)}";
            rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Ready,
                $"APEX {payload.ApexVersion} · pid {backendInfo.Pid?.ToString() ?? "?"} · port {m.Port}{age}"));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Ready, $"requête authentifiée réussie (API {payload.ApiVersion})"));
            foreach (var (key, name) in ComponentKeys.BackendServices)
            {
                var measured = payload.Components.FirstOrDefault(c => c.Name == name);
                rows.Add(measured is null
                    ? new ComponentState(key, ComponentStatus.Unknown, "non rapporté par le backend")
                    : new ComponentState(key, MapState(measured.State), measured.Detail));
            }
            var notReady = rows.Where(r => r.Status != ComponentStatus.Ready).ToList();
            return notReady.Count == 0
                ? Snapshot(PanelVerdict.Ready, NotReadyCause.None, "Tous les composants d'APEX répondent.")
                : Snapshot(PanelVerdict.Degraded, NotReadyCause.PartialServices,
                    "L'API APEX répond, mais certains services ne sont pas prêts : "
                    + string.Join(" ; ", notReady.Select(r => $"{r.Label} ({Label(r.Status)} — {r.Detail})")) + ".");
        }

        // 3. Pas de réponse valide : pourquoi ?
        switch (m.Fetch.Outcome)
        {
            case FetchOutcome.OldBackend:
                rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Ready, "un backend APEX répond"));
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error, m.Fetch.Detail));
                Services(ComponentStatus.Unknown, "non interprété : backend antérieur au Panel");
                return Snapshot(PanelVerdict.VersionMismatch, NotReadyCause.BackendTooOld,
                    $"Le backend APEX en cours d'exécution est antérieur à ce Panel. Mettez à jour le dépôt APEX {where}, arrêtez le backend puis relancez {LaunchCommand}.");

            case FetchOutcome.Unauthorized:
                rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Ready, "un backend répond sur le port " + m.Port));
                if (m.Token.Kind == TokenKind.Ok)
                {
                    rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error, "jeton refusé (401)"));
                    Services(ComponentStatus.Unknown, "non mesuré : accès refusé");
                    return Snapshot(PanelVerdict.NotReady, NotReadyCause.TokenRejected,
                        "Le backend refuse le jeton d'accès lu sur l'hôte : il a été démarré depuis un autre dossier ou une autre distribution que celle enregistrée. Arrêtez les autres backends APEX puis relancez " + LaunchCommand + ".");
                }
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error,
                    m.Discovery.Kind == DiscoveryKind.Found ? m.Token.Detail : "API protégée, et aucun backend APEX enregistré : jeton introuvable"));
                Services(ComponentStatus.Unknown, "non mesuré : jeton indisponible");
                return Snapshot(PanelVerdict.NotReady, NotReadyCause.TokenMissing,
                    "Le Panel ne trouve pas le jeton d'accès de l'API (" + m.Token.Detail + "). Relancez le backend avec le launch.sh dédié (" + LaunchCommand + ") : il enregistre l'emplacement du jeton pour le Panel.");

            case FetchOutcome.NotApex:
                rows.Add(new ComponentState(ComponentKey.Backend,
                    m.Discovery.Kind == DiscoveryKind.Found ? ComponentStatus.Error : ComponentStatus.Offline,
                    m.Discovery.Kind == DiscoveryKind.Found ? "enregistré, mais son port est occupé par un autre programme" : "aucun backend APEX enregistré"));
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error, m.Fetch.Detail));
                Services(ComponentStatus.Unknown, "non mesuré : API APEX indisponible");
                return Snapshot(PanelVerdict.NotReady, NotReadyCause.PortConflict,
                    $"Un autre programme utilise le port {m.Port}. Fermez-le, ou démarrez APEX sur un autre port ({LaunchCommand} --port <port>) : le Panel le trouvera automatiquement.");

            case FetchOutcome.Timeout:
                rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Degraded, "accepte la connexion mais ne répond pas à temps"));
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error, m.Fetch.Detail));
                Services(ComponentStatus.Unknown, "non mesuré : API sans réponse");
                return Snapshot(PanelVerdict.NotReady, NotReadyCause.ApiTimeout,
                    "Le backend APEX ne répond pas à temps (machine surchargée ou backend bloqué). Le Panel réessaie automatiquement ; si cela persiste, cliquez sur « Arrêter APEX » puis " + StartButton + ".");

            case FetchOutcome.HttpError:
            case FetchOutcome.Failed:
                rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Degraded, "répond, mais en erreur"));
                rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error, m.Fetch.Detail));
                Services(ComponentStatus.Unknown, "non mesuré : API en erreur");
                return Snapshot(PanelVerdict.NotReady, NotReadyCause.ApiError,
                    $"L'API APEX répond en erreur ({m.Fetch.Detail}). Consultez la sortie de launch.sh {where}.");
        }

        // 4. Connexion refusée depuis Windows alors que le processus vit ET écoute DANS WSL (constaté dans
        //    /proc/net/tcp de la distribution) : ce n'est ni un arrêt ni un blocage du backend, c'est la
        //    redirection localhost WSL2 -> Windows qui manque (défaut W2 de l'audit).
        if (m.Host.Applicable && m.Fetch.Outcome == FetchOutcome.Refused && m.BackendProcessAlive == true && m.ListeningInHost == true)
        {
            rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Ready,
                $"écoute dans WSL sur le port {m.Port} (pid {record?.Pid.ToString() ?? "?"})"));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Error,
                $"injoignable depuis Windows : 127.0.0.1:{m.Port} refusé"));
            Services(ComponentStatus.Unknown, "non mesuré : API injoignable depuis Windows");
            return Snapshot(PanelVerdict.NotReady, NotReadyCause.LocalhostUnreachable,
                $"Le backend APEX tourne et écoute dans WSL (port {m.Port}), mais Windows ne l'atteint pas par localhost. "
                + "Vérifiez que la redirection localhost de WSL2 n'est pas désactivée (localhostForwarding=false ou mode réseau "
                + "particulier dans %UserProfile%\\.wslconfig), et qu'un VPN ou un pare-feu ne la bloque pas ; puis exécutez "
                + "« wsl --shutdown » dans PowerShell et relancez " + LaunchCommand + ".");
        }

        // 5. Rien n'écoute (connexion refusée) ou pas de tentative.
        if (m.Discovery.Kind == DiscoveryKind.Found && record is { IsRefused: true, Refusal: { } refusal })
        {
            return Refused(m, rows, refusal, Snapshot, Services);
        }
        if (m.Discovery.Kind != DiscoveryKind.Found && m.Discovery.Kind != DiscoveryKind.Invalid && m.Launch is { } launch
            && LaunchFailure(launch) is { } failure)
        {
            rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Offline, failure.Row));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Offline, $"rien n'écoute sur 127.0.0.1:{m.Port}"));
            Services(ComponentStatus.Unknown, "non mesuré : APEX non démarré");
            return Snapshot(PanelVerdict.NotReady, NotReadyCause.StartFailed, failure.Guidance);
        }
        if (m.Discovery.Kind == DiscoveryKind.Missing && m.Launch is { Running: true } running && m.Now - running.StartedAt < StartupGrace)
        {
            rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Starting, $"démarrage demandé {Ago(m.Now - running.StartedAt)} : launch.sh démarre"));
            rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Offline, $"rien n'écoute encore sur 127.0.0.1:{m.Port}"));
            Services(ComponentStatus.Unknown, "non mesuré : démarrage en cours");
            return Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendStarting,
                "Démarrage d'APEX demandé : launch.sh prépare APEX (Docker, doctor, Startup Gate). Le Panel se met à jour automatiquement.");
        }
        rows.Add(BackendWhenSilent(m));
        rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Offline,
            m.Fetch.Outcome == FetchOutcome.Refused ? $"rien n'écoute sur 127.0.0.1:{m.Port}" : m.Fetch.Detail));
        Services(ComponentStatus.Unknown, "non mesuré : backend indisponible");
        var backend = rows.First(r => r.Key == ComponentKey.Backend);
        if (m.Discovery.Kind == DiscoveryKind.Invalid)
        {
            return Snapshot(PanelVerdict.NotReady, NotReadyCause.DiscoveryInvalid,
                "L'enregistrement du backend est illisible (" + m.Discovery.Detail + "). Relancez " + LaunchCommand + " : il le réécrit.");
        }
        return backend.Status switch
        {
            ComponentStatus.Starting => Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendStarting,
                "Le backend APEX démarre (" + backend.Detail + ")… Le Panel se met à jour automatiquement."),
            ComponentStatus.Error => Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendUnresponsive,
                $"Le processus du backend existe mais son API ne répond pas. Cliquez sur « Arrêter APEX » puis {StartButton}."),
            _ when m.Discovery.Kind == DiscoveryKind.Found => Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendStopped,
                $"Le backend APEX s'est arrêté (arrêt brutal ou fermeture de sa fenêtre). Cliquez sur {StartButton} pour le relancer."),
            _ => Snapshot(PanelVerdict.NotReady, NotReadyCause.BackendNotStarted,
                $"Le backend APEX n'est pas démarré. Cliquez sur {StartButton} (ou {where}, dans le dossier APEX : {LaunchCommand})."),
        };
    }

    /// <summary>
    /// Le dernier démarrage n'a pas eu lieu. Une VÉRIFICATION qui refuse (Startup Gate, apex doctor) est
    /// BLOCKED — le Panel ne la contourne jamais et ne propose jamais de la contourner ; une panne
    /// (Docker, erreur interne du Gate) est NOT READY. La phase 2 n'y joue aucun rôle.
    /// </summary>
    private static PanelSnapshot Refused(
        StatusInputs m, List<ComponentState> rows, StartRefusal refusal,
        Func<PanelVerdict, NotReadyCause, string, VersionInfo?, PanelSnapshot> snapshot, Action<ComponentStatus, string> services)
    {
        var at = m.Discovery.Record is { } r ? $" ({Ago(m.Now - r.StartedAt)})" : "";
        var (row, guidance) = refusal.ExitCode switch
        {
            5 => ("démarrage refusé par le Startup Gate : contrôle mesuré en FAIL",
                "Le Startup Gate a refusé le démarrage d'APEX" + at + " : contrôle(s) MESURÉ(S) en FAIL : "
                + Ids(refusal.Blocking) + ". Corrigez ce point (détail dans « Journal du démarrage »), puis cliquez sur " + StartButton + "."),
            6 => ("démarrage refusé par le Startup Gate : précondition non vérifiée",
                "Le Startup Gate a refusé le démarrage d'APEX" + at + " : une précondition n'a pas pu être vérifiée (ce n'est pas un FAIL) : "
                + Ids(refusal.UnverifiedBlocking) + ". Rendez-la vérifiable (détail dans « Journal du démarrage »), puis cliquez sur " + StartButton + "."),
            4 => ("démarrage refusé par apex doctor (FAIL)",
                "apex doctor a signalé un échec : APEX ne démarre pas" + at + ". Le rapport est dans « Journal du démarrage » ; corrigez puis cliquez sur " + StartButton + "."),
            2 => ("démarrage impossible : Docker / docker compose",
                "L'infrastructure d'APEX (Docker, docker compose) n'a pas démarré" + at + ". Vérifiez que Docker fonctionne dans WSL (détail dans « Journal du démarrage »), puis cliquez sur " + StartButton + "."),
            7 => ("démarrage impossible : erreur interne du Startup Gate",
                "Le Startup Gate s'est terminé en erreur interne" + at + " — ce n'est pas une décision du Gate. Détail dans « Journal du démarrage »."),
            _ => ($"démarrage refusé (code {refusal.ExitCode})",
                $"Le démarrage d'APEX a échoué{at} (code {refusal.ExitCode}). Détail dans « Journal du démarrage »."),
        };
        rows.Add(new ComponentState(ComponentKey.Backend, ComponentStatus.Offline, row));
        rows.Add(new ComponentState(ComponentKey.Api, ComponentStatus.Offline, "APEX n'a pas démarré"));
        services(ComponentStatus.Unknown, "non mesuré : APEX n'a pas démarré");
        return refusal.IsVerdict
            ? snapshot(PanelVerdict.Blocked, NotReadyCause.StartupRefused, guidance, null)
            : snapshot(PanelVerdict.NotReady, NotReadyCause.StartFailed, guidance, null);
    }

    private static string Ids(IReadOnlyList<string> ids) => ids.Count == 0 ? "voir le journal" : string.Join(", ", ids);

    /// <summary>
    /// Un démarrage demandé par CE Panel qui s'est terminé sans laisser d'enregistrement (échec avant que
    /// Python soit trouvé, déjà en cours, script introuvable) : le code de sortie de launch.sh dit pourquoi.
    /// </summary>
    internal static (string Row, string Guidance)? LaunchFailure(LaunchAttempt launch)
    {
        if (launch.FailureReason is { } reason)
        {
            return ("démarrage impossible", $"Le Panel n'a pas pu lancer le démarrage d'APEX ({reason}). Vérifiez que WSL est installé.");
        }
        return launch.ExitCode switch
        {
            null or 0 => null,
            130 or 137 or 143 => null, // interrompu / arrêté volontairement (« Arrêter APEX », Ctrl+C) : pas un échec
            1 => ("démarrage refusé : .env absent",
                "Le fichier .env d'APEX est absent dans le dossier APEX (WSL). Créez-le (cp .env.example .env, puis éditez les valeurs) et cliquez sur " + StartButton + "."),
            3 => ("démarrage refusé : Python 3.12+ absent",
                "Aucun Python 3.12+ utilisable dans le dossier APEX (.venv). Créez l'environnement Python d'APEX (voir l'installation), puis cliquez sur " + StartButton + "."),
            8 => ("déjà en cours de démarrage ou d'exécution",
                "APEX est déjà en cours de démarrage ou d'exécution : rien n'a été relancé. Le Panel le trouvera automatiquement."),
            126 or 127 => ("launch.sh introuvable",
                $"Le dossier APEX « {launch.ProjectRoot} » ne contient pas scripts/apex-launch.sh. Indiquez le bon dossier dans Paramètres (« Dossier APEX dans WSL »), puis cliquez sur {StartButton}."),
            var code => ($"démarrage échoué (code {code})",
                $"Le démarrage d'APEX s'est terminé en erreur (code {code}). Détail dans « Journal du démarrage »."),
        };
    }

    private static ComponentState BackendWhenSilent(StatusInputs m)
    {
        var record = m.Discovery.Record;
        if (m.Discovery.Kind != DiscoveryKind.Found || record is null)
        {
            return new ComponentState(ComponentKey.Backend,
                m.Discovery.Kind == DiscoveryKind.Invalid ? ComponentStatus.Error : ComponentStatus.Offline,
                m.Discovery.Kind == DiscoveryKind.Invalid ? m.Discovery.Detail : "launch.sh n'a pas été exécuté (aucun backend enregistré)");
        }
        var age = m.Now - record.StartedAt;
        if (m.BackendProcessAlive == false)
        {
            return new ComponentState(ComponentKey.Backend, ComponentStatus.Offline, record.IsStarting
                ? $"launch.sh (pid {record.Pid}) s'est arrêté avant de démarrer l'API"
                : $"arrêté : le processus {record.Pid} n'existe plus (enregistrement périmé, démarré {Ago(age)})");
        }
        if (record.IsStarting)
        {
            // Préflight en cours (Docker, doctor, gate) : aucune limite de durée tant que le processus vit —
            // un premier lancement peut télécharger des images pendant plusieurs minutes.
            return new ComponentState(ComponentKey.Backend, ComponentStatus.Starting,
                $"launch.sh prépare le démarrage (Docker, doctor, gate) depuis {Ago(age)}");
        }
        if (age < StartupGrace)
        {
            return new ComponentState(ComponentKey.Backend, ComponentStatus.Starting, $"en démarrage (pid {record.Pid}, depuis {Ago(age)})");
        }
        return m.BackendProcessAlive == true
            ? new ComponentState(ComponentKey.Backend, ComponentStatus.Error, $"processus {record.Pid} présent mais muet depuis {Ago(age)}")
            : new ComponentState(ComponentKey.Backend, ComponentStatus.Offline, $"enregistré (pid {record.Pid}) mais ne répond plus");
    }

    public static ComponentStatus MapState(string state) => state switch
    {
        "ready" => ComponentStatus.Ready,
        "degraded" => ComponentStatus.Degraded,
        "offline" => ComponentStatus.Offline,
        "error" => ComponentStatus.Error,
        "starting" => ComponentStatus.Starting,
        _ => ComponentStatus.Unknown,
    };

    public static string Headline(PanelVerdict verdict) => verdict switch
    {
        PanelVerdict.Ready => "APEX READY",
        PanelVerdict.Degraded => "APEX DEGRADED",
        PanelVerdict.VersionMismatch => "VERSION_MISMATCH",
        PanelVerdict.Blocked => "APEX BLOCKED",
        _ => "APEX NOT READY",
    };

    public static string Label(ComponentStatus status) => status switch
    {
        ComponentStatus.Ready => "READY",
        ComponentStatus.Starting => "STARTING",
        ComponentStatus.Offline => "OFFLINE",
        ComponentStatus.Degraded => "DEGRADED",
        ComponentStatus.Error => "ERROR",
        _ => "UNKNOWN",
    };

    private static string Ago(TimeSpan span) =>
        span < TimeSpan.Zero ? "à l'instant"
        : span.TotalSeconds < 60 ? $"il y a {span.TotalSeconds:0} s"
        : span.TotalMinutes < 60 ? $"il y a {span.TotalMinutes:0} min"
        : $"il y a {span.TotalHours:0} h";
}
