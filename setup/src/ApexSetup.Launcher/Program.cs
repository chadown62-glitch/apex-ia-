using ApexSetup.Application;
using ApexSetup.Launcher.Elevation;
using ApexSetup.Launcher.Notify;
using ApexSetup.Launcher.ProcessLaunch;

namespace ApexSetup.Launcher;

/// <summary>
/// Point d'entrée du launcher de production — orchestre, sans jamais les
/// réimplémenter : (1) l'état d'installation/Runtime/Gate via
/// <see cref="ISetupApplicationService"/> (jamais <c>ApexSetup.Core</c>
/// directement — même frontière que le CLI et le GUI Setup) ; (2)
/// l'élévation Windows via <see cref="IElevationContext"/>/<see cref="IProcessLauncher"/>
/// (jamais une seconde fois si déjà élevé — pas de boucle UAC) ; (3) le
/// démarrage réel d'APEX via le MÊME mécanisme que <c>apex.bat</c>
/// (<c>wsl.exe -- bash scripts/apex-launch.sh serve</c>), jamais un second
/// chemin de démarrage inventé.
/// </summary>
public static class Program
{
    private const string ElevatedRelaunchFlag = "--elevated-relaunch";

    public static async Task<int> Main(string[] args)
    {
        try
        {
            return await RunAsync(args, CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // Filet de sécurité générique — voir ApexSetup.Cli.Program pour
            // le même principe : une exception non anticipée devient un
            // code de sortie déterministe, jamais une trace de pile brute
            // affichée à l'utilisateur final.
            Console.Error.WriteLine($"erreur interne du launcher : {ex.Message}");
            return (int)LauncherExitCode.InternalError;
        }
    }

    internal static async Task<int> RunAsync(string[] args, CancellationToken cancellationToken)
    {
        var alreadyElevatedRelaunch = Array.IndexOf(args, ElevatedRelaunchFlag) >= 0;
        var passthroughArgs = Array.FindAll(args, a => a != ElevatedRelaunchFlag);

        // Le répertoire du launcher EST la racine d'installation APEX —
        // même principe d'auto-localisation que apex.bat (`%~dp0`) et
        // scripts/apex-launch.sh (`BASH_SOURCE[0]`/resolve_project_root) :
        // aucun registre d'installation séparé n'est inventé, le launcher
        // ne fonctionne que packagé À L'INTÉRIEUR du dossier APEX qu'il
        // sait démarrer.
        var destination = AppContext.BaseDirectory;

        IElevationContext elevation = OperatingSystem.IsWindows()
            ? new WindowsElevationContext()
            : new NullElevationContext();
        IProcessLauncher processLauncher = new RealProcessLauncher();
        IUserNotifier notifier = OperatingSystem.IsWindows()
            ? new WindowsTaskDialogNotifier()
            : new ConsoleUserNotifier();

        var (runtimeExecutable, runtimeArgs) = BuildRuntimeInvocation();
        var service = SetupApplicationServiceFactory.Create(runtimeExecutable, runtimeArgs);

        var decision = await DetectAsync(service, destination, cancellationToken).ConfigureAwait(false);
        LogDecision(decision);

        switch (decision.Outcome)
        {
            case LauncherOutcome.Ready:
                return await HandleReadyAsync(
                    destination, elevation, processLauncher, notifier,
                    alreadyElevatedRelaunch, passthroughArgs, cancellationToken).ConfigureAwait(false);

            case LauncherOutcome.NeedsRecovery:
                return await HandleRecoveryAsync(service, destination, processLauncher, notifier, cancellationToken).ConfigureAwait(false);

            case LauncherOutcome.NeedsSetup:
            case LauncherOutcome.InvalidInstall:
                return await HandleNeedsSetupAsync(destination, processLauncher, notifier, decision).ConfigureAwait(false);

            case LauncherOutcome.Blocked:
                notifier.ShowError(
                    "APEX ne peut pas démarrer",
                    "Le contrôle de sécurité d'APEX (Gate) refuse l'autorisation de démarrer sur cette machine.",
                    decision.Detail, offerRepair: false);
                return (int)LauncherExitCode.Blocked;

            case LauncherOutcome.RuntimeError:
                notifier.ShowError(
                    "APEX ne répond pas",
                    "Le moteur APEX ne répond pas correctement. Vérifiez que WSL2 et l'infrastructure locale sont accessibles.",
                    decision.Detail, offerRepair: false);
                return (int)LauncherExitCode.RuntimeError;

            case LauncherOutcome.NeedsUpdate:
                notifier.ShowError(
                    "Mise à jour requise",
                    "Une version plus récente d'APEX est nécessaire avant de continuer.",
                    decision.Detail, offerRepair: false);
                return (int)LauncherExitCode.NeedsUpdate;

            default:
                notifier.ShowError(
                    "Erreur inconnue",
                    "Une situation inattendue empêche APEX de démarrer. Consultez les journaux du launcher.",
                    decision.Detail, offerRepair: false);
                return (int)LauncherExitCode.UnknownError;
        }
    }

    /// <summary>
    /// <c>ISetupApplicationService</c> parle au Runtime via
    /// <c>RuntimeAdapter</c>, générique sur « exécutable + arguments de
    /// base » précisément pour ce cas (voir sa docstring et docs/CONTRACTS.md :
    /// « en production, wsl.exe -- apex ») — jamais réimplémenté ici,
    /// seulement configuré. Sous Windows, l'exécutable "apex" littéral
    /// n'existe pas dans l'environnement du launcher (il vit dans WSL2) :
    /// sans ce réglage, StatusAsync/ValidateAsync échoueraient
    /// systématiquement (ProcessNotFound) sur toute machine réelle.
    /// Respecte APEX_WSL_DISTRO comme <see cref="LaunchApexAsync"/> —
    /// jamais deux mécanismes de sélection de distribution différents.
    /// </summary>
    private static (string? Executable, IReadOnlyList<string>? Args) BuildRuntimeInvocation()
    {
        if (!OperatingSystem.IsWindows())
        {
            return (null, null); // valeurs par défaut de SetupApplicationServiceFactory ("apex", []).
        }

        var args = new List<string>();
        var distro = Environment.GetEnvironmentVariable("APEX_WSL_DISTRO");
        if (!string.IsNullOrWhiteSpace(distro))
        {
            args.Add("-d");
            args.Add(distro);
        }

        args.Add("--");
        args.Add("apex");
        return ("wsl.exe", args);
    }

    private static async Task<LauncherDecision> DetectAsync(
        ISetupApplicationService service, string destination, CancellationToken cancellationToken)
    {
        var status = await service.StatusAsync(destination, cancellationToken).ConfigureAwait(false);

        // ValidateAsync suppose une installation terminée (voir sa
        // docstring dans ISetupApplicationService) — ne l'appeler que si
        // StatusAsync rapporte "completed", jamais spéculativement.
        var validate = status.State == "completed"
            ? await service.ValidateAsync(cancellationToken).ConfigureAwait(false)
            : null;

        return LauncherStateEngine.Derive(status, validate, minimumRuntimeVersion: null);
    }

    private static async Task<int> HandleReadyAsync(
        string destination, IElevationContext elevation, IProcessLauncher processLauncher, IUserNotifier notifier,
        bool alreadyElevatedRelaunch, string[] passthroughArgs, CancellationToken cancellationToken)
    {
        if (!alreadyElevatedRelaunch && elevation.RequiresElevationFor(destination))
        {
            var exePath = Environment.ProcessPath;
            if (exePath is null)
            {
                notifier.ShowError("Élévation impossible", "Le chemin de l'exécutable du launcher est introuvable.", "Environment.ProcessPath == null", offerRepair: false);
                return (int)LauncherExitCode.InternalError;
            }

            var relaunchArgs = new List<string>(passthroughArgs) { ElevatedRelaunchFlag };
            var result = processLauncher.RelaunchElevated(exePath, relaunchArgs);
            if (!result.Started)
            {
                if (result.FailureReason == "uac_refused")
                {
                    notifier.ShowError(
                        "Élévation refusée",
                        "APEX nécessite des droits administrateur pour continuer, et la demande a été refusée.",
                        "UAC refusé par l'utilisateur", offerRepair: false);
                    return (int)LauncherExitCode.ElevationDeclined;
                }

                notifier.ShowError("Élévation impossible", "APEX n'a pas pu obtenir les droits nécessaires.", result.FailureReason ?? "raison inconnue", offerRepair: false);
                return (int)LauncherExitCode.InternalError;
            }

            // Le processus élevé continue en parallèle — celui-ci n'a
            // plus rien à faire (jamais deux instances qui tentent toutes
            // deux de démarrer APEX simultanément).
            return (int)LauncherExitCode.Success;
        }

        return await LaunchApexAsync(destination, processLauncher, notifier, passthroughArgs, cancellationToken).ConfigureAwait(false);
    }

    private static async Task<int> LaunchApexAsync(
        string destination, IProcessLauncher processLauncher, IUserNotifier notifier,
        string[] passthroughArgs, CancellationToken cancellationToken)
    {
        // Même nuance que apex.bat (§ APEX_WSL_DISTRO) — jamais dupliquée
        // silencieusement : une distribution WSL non par défaut reste
        // sélectionnable identiquement des deux launchers.
        var distro = Environment.GetEnvironmentVariable("APEX_WSL_DISTRO");
        var distroArgs = string.IsNullOrWhiteSpace(distro) ? Array.Empty<string>() : new[] { "-d", distro };

        // bash (dans WSL) attend un chemin Linux, jamais un chemin Windows
        // (`C:\...`) — exactement la même conversion que apex.bat, via le
        // même utilitaire natif `wslpath` (jamais réimplémentée à la main :
        // les règles de wslpath, réseau UNC compris, sont déjà correctes
        // côté WSL, un second algorithme ici serait une source de dérive).
        var wslPathArgs = new List<string>(distroArgs) { "wslpath", "-a", destination };
        var pathResult = await processLauncher.CaptureOutputAsync("wsl.exe", wslPathArgs, destination, cancellationToken).ConfigureAwait(false);
        var wslDestination = pathResult.Stdout?.Trim();
        if (!pathResult.Started || pathResult.ExitCode != 0 || string.IsNullOrEmpty(wslDestination))
        {
            notifier.ShowError(
                "APEX n'a pas pu démarrer",
                "Impossible de convertir le chemin d'installation en chemin WSL2. Vérifiez que WSL2 et une distribution sont correctement installés.",
                pathResult.FailureReason ?? "wslpath n'a produit aucune sortie", offerRepair: false);
            return (int)LauncherExitCode.RuntimeError;
        }

        var wslArgs = new List<string>(distroArgs)
        {
            "--", "bash", $"{wslDestination}/scripts/apex-launch.sh", "serve",
        };
        wslArgs.AddRange(passthroughArgs);

        var result = await processLauncher.RunAsync("wsl.exe", wslArgs, destination, cancellationToken).ConfigureAwait(false);
        if (!result.Started)
        {
            notifier.ShowError(
                "APEX n'a pas pu démarrer",
                "Impossible de lancer l'environnement WSL2. Vérifiez que WSL2 est installé et accessible.",
                result.FailureReason ?? "raison inconnue", offerRepair: false);
            return (int)LauncherExitCode.RuntimeError;
        }

        return result.ExitCode ?? (int)LauncherExitCode.UnknownError;
    }

    private static async Task<int> HandleRecoveryAsync(
        ISetupApplicationService service, string destination, IProcessLauncher processLauncher,
        IUserNotifier notifier, CancellationToken cancellationToken)
    {
        var action = notifier.ShowError(
            "Installation interrompue",
            "L'installation d'APEX a été interrompue. Une réparation automatique est disponible.",
            "SetupLifecycleState.RecoveryNeeded", offerRepair: true);

        if (action != UserAction.Repair)
        {
            return (int)LauncherExitCode.RecoveryRequired;
        }

        var recovered = await service.RecoverAsync(destination, cancellationToken).ConfigureAwait(false);
        notifier.ShowInfo("Réparation terminée", recovered.Detail);

        if (!recovered.Success)
        {
            return (int)LauncherExitCode.RecoveryRequired;
        }

        // Une seule re-détection après réparation — jamais une boucle : si
        // l'état n'est pas immédiatement exploitable après une réparation
        // qui s'est elle-même déclarée réussie, c'est une situation que
        // l'utilisateur doit voir plutôt qu'un launcher qui boucle
        // silencieusement.
        var decision = await DetectAsync(service, destination, cancellationToken).ConfigureAwait(false);
        if (decision.Outcome != LauncherOutcome.Ready)
        {
            notifier.ShowError(
                "APEX ne peut toujours pas démarrer",
                "La réparation a réussi mais APEX n'est pas encore prêt à démarrer.",
                decision.Detail, offerRepair: false);
            return (int)LauncherExitCode.RecoveryRequired;
        }

        return await LaunchApexAsync(destination, processLauncher, notifier, [], cancellationToken).ConfigureAwait(false);
    }

    private static async Task<int> HandleNeedsSetupAsync(
        string destination, IProcessLauncher processLauncher, IUserNotifier notifier, LauncherDecision decision)
    {
        var action = notifier.ShowError(
            "APEX doit être installé",
            "Aucune installation APEX exploitable n'a été trouvée. Le programme d'installation peut la réaliser maintenant.",
            decision.Detail, offerRepair: true);

        if (action != UserAction.Repair)
        {
            return decision.Outcome == LauncherOutcome.InvalidInstall
                ? (int)LauncherExitCode.InvalidInstall
                : (int)LauncherExitCode.NeedsSetup;
        }

        // Réutilise le GUI Setup EXISTANT — jamais une seconde interface
        // d'installation. Le GUI est publié à côté du launcher dans le
        // même paquet de release (voir setup/docs/PACKAGING.md).
        var setupGuiPath = Path.Combine(AppContext.BaseDirectory, "ApexSetup.Gui.exe");
        var result = await processLauncher.RunAsync(setupGuiPath, [], destination, CancellationToken.None).ConfigureAwait(false);

        if (!result.Started)
        {
            notifier.ShowError("Setup introuvable", "Le programme d'installation d'APEX est introuvable dans ce paquet.", setupGuiPath, offerRepair: false);
            return (int)LauncherExitCode.InvalidInstall;
        }

        return (int)LauncherExitCode.Success;
    }

    private static void LogDecision(LauncherDecision decision)
    {
        // §G Logging : suffisant pour diagnostiquer (état détecté, jamais
        // de secret — cette décision ne porte aucune valeur d'environnement
        // ni de jeton). stderr, jamais stdout (même discipline que
        // scripts/apex-launch.sh — voir son log_info).
        Console.Error.WriteLine($"[apex-launcher] état détecté : {decision.Outcome} — {decision.Detail}");
    }
}
