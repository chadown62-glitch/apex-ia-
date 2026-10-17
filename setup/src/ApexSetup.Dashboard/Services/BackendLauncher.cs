namespace ApexSetup.Dashboard.Services;

public enum HostKind
{
    Wsl,
    Local,
    /// <summary>APEX autonome sur ce poste Windows (sans WSL) : <c>python -m apex.standalone</c>.</summary>
    Native,
}

/// <summary>Un démarrage demandé par CE Panel (bouton ou <c>--start</c>) : ce qui en est connu, jamais supposé.</summary>
public sealed record LaunchAttempt(DateTimeOffset StartedAt, string ProjectRoot, int? ExitCode = null, string? FailureReason = null)
{
    /// <summary>Lancé et pas encore terminé : launch.sh prépare APEX, puis APEX tourne (même processus).</summary>
    public bool Running => FailureReason is null && ExitCode is null;
}

/// <summary>Un processus lancé SANS attendre sa fin (le démarrage d'APEX dure autant qu'APEX).</summary>
public sealed record DetachedProcess(bool Started, string? FailureReason, Task<int> Exited)
{
    public static DetachedProcess NotStarted(string reason) => new(false, reason, Task.FromResult(-1));
}

/// <summary>Lancement d'un processus de longue durée — argv direct, sans fenêtre, sans tube vers le Panel.</summary>
public interface IDetachedProcessStarter
{
    DetachedProcess Start(string executable, IReadOnlyList<string> arguments);
}

/// <summary>
/// « Démarrer APEX » / « Arrêter APEX » : le Panel n'a AUCUN chemin de démarrage à lui. Il exécute le
/// launcher officiel, <c>scripts/apex-launch.sh</c>, exactement comme un utilisateur dans WSL :
/// <list type="bullet">
/// <item><c>panel-serve</c> = <c>serve</c> (même préflight, même Startup Gate — jamais contourné, aucune
/// option ajoutée) avec la sortie dans <c>~/.apex/runtime/launch.log</c> au lieu d'un terminal ;</item>
/// <item><c>stop</c> = arrêt du backend enregistré (pid vérifié comme APEX) puis de l'infrastructure
/// que launch.sh a démarrée.</item>
/// </list>
/// Sous Windows : <c>wsl.exe [-d D] --cd ~ --exec bash &lt;dossier&gt;/scripts/apex-launch.sh &lt;commande&gt;</c>
/// — argv direct, jamais un shell qui interpréterait le dossier. Le dossier vient des Paramètres ou du
/// dernier backend qui a répondu AVEC son jeton, jamais d'un enregistrement de découverte non vérifié :
/// un fichier altéré dans WSL ne peut pas choisir ce que le Panel exécute (voir S04). Rien n'est
/// jamais lancé sans un geste de l'utilisateur.
/// </summary>
/// <para>
/// Mode Windows NATIF (<paramref name="native"/>) : le launcher officiel est <c>python -m apex.standalone</c> (même
/// préflight, même Startup Gate, mêmes codes de sortie que launch.sh), exécuté par le Python préparé d'APEX —
/// exécutable et arguments FIXES, rien ne vient d'un enregistrement de découverte ni des Paramètres.
/// </para>
public sealed class BackendLauncher(IDetachedProcessStarter starter, IProcessRunner runner, bool wsl, string? localHome = null,
    NativeRuntime? native = null)
{
    public const string Script = "scripts/apex-launch.sh";
    public const string StartCommand = "panel-serve";
    public const string StopCommand = "stop";
    public const string LaunchLog = ".apex/runtime/launch.log";
    public static readonly TimeSpan StopTimeout = TimeSpan.FromSeconds(120);

    private readonly string _home = localHome ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    /// <summary>Chemin du script, relatif au dossier personnel ou absolu ; <c>null</c> si le dossier est refusé.</summary>
    public static string? ScriptPath(string projectRoot)
    {
        var root = projectRoot.Trim();
        if (root.StartsWith("~/", StringComparison.Ordinal))
        {
            root = root[2..];
        }
        root = root.TrimEnd('/');
        return root.Length > 0 && (HostPaths.IsSafePosixAbsolute(root) || HostPaths.IsSafeRelative(root))
            ? $"{root}/{Script}"
            : null;
    }

    public NativeRuntime? Native => native;

    /// <summary>Exécutable et arguments, exactement — exposés pour les tests et la liste autorisée de la validation Windows.</summary>
    public (string Executable, List<string> Arguments)? Command(string? distro, string projectRoot, string command)
    {
        if (native is not null)
        {
            var python = native.PythonExecutable(native.TryReadSpec(out _));
            return command == StartCommand
                ? (python, NativeRuntime.PythonArguments("-m", "apex.standalone", "serve", "--log"))
                : (python, NativeRuntime.PythonArguments("-m", "apex.standalone", "stop"));
        }
        var script = ScriptPath(projectRoot);
        if (script is null)
        {
            return null;
        }
        if (!wsl)
        {
            return ("bash", [script.StartsWith('/') ? script : Path.Combine(_home, script), command]);
        }
        var args = new List<string>();
        if (!string.IsNullOrWhiteSpace(distro))
        {
            args.Add("-d");
            args.Add(distro);
        }
        args.AddRange(["--cd", "~", "--exec", "bash", script, command]);
        return ("wsl.exe", args);
    }

    public (LaunchAttempt Attempt, Task<int> Exited) Start(string? distro, string projectRoot, DateTimeOffset now)
    {
        if (Command(distro, projectRoot, StartCommand) is not { } command)
        {
            return (new LaunchAttempt(now, projectRoot, FailureReason: $"dossier APEX refusé : « {projectRoot} »"), Task.FromResult(-1));
        }
        var process = starter.Start(command.Executable, command.Arguments);
        return process.Started
            ? (new LaunchAttempt(now, projectRoot), process.Exited)
            : (new LaunchAttempt(now, projectRoot, FailureReason: process.FailureReason ?? "processus non démarré"), Task.FromResult(-1));
    }

    public Task<ProcessRunResult> StopAsync(string? distro, string projectRoot, CancellationToken cancellationToken = default) =>
        Command(distro, projectRoot, StopCommand) is { } command
            ? runner.RunAsync(command.Executable, command.Arguments, StopTimeout, cancellationToken)
            : Task.FromResult(ProcessRunResult.NotStarted($"dossier APEX refusé : « {projectRoot} »"));
}

/// <summary>Ce dont le Panel a besoin pour AGIR (et non plus seulement observer) — absent, les boutons restent inactifs.</summary>
public sealed record PanelControl(BackendLauncher Launcher, StatusClient Client, IBrowserLauncher Browser);
