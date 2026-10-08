using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>État de la machine qui exécute le backend (distribution WSL sous Windows ; la machine elle-même sous Linux).</summary>
/// <param name="Applicable">Faux sur un hôte Linux natif : il n'y a pas de WSL, la ligne n'est pas affichée.</param>
/// <param name="Status">État de WSL (sans objet si non applicable).</param>
/// <param name="Running">Vrai si l'on peut lire des fichiers de l'hôte SANS le démarrer.</param>
/// <param name="RunningDistros">Toutes les distributions WSL en cours d'exécution (où chercher le backend sans rien démarrer).</param>
/// <param name="Runtime">Mode « Windows natif » (APEX autonome, sans WSL) : l'état du runtime APEX de ce poste ; <c>null</c> sinon.</param>
public sealed record HostProbe(
    bool Applicable, ComponentStatus Status, bool Running, string Detail, string? Distro, NotReadyCause Cause,
    IReadOnlyList<string>? RunningDistros = null, RuntimeInspection? Runtime = null)
{
    public static HostProbe LocalHost() => new(false, ComponentStatus.Ready, true, "hôte Linux natif (sans WSL)", null, NotReadyCause.None);

    /// <summary>APEX autonome sur ce poste : pas de WSL, l'hôte « tourne » toujours ; seul le runtime peut manquer.</summary>
    public static HostProbe Native(RuntimeInspection runtime) => new(false,
        runtime.State == RuntimeState.Ready ? ComponentStatus.Ready : runtime.State == RuntimeState.Broken ? ComponentStatus.Error : ComponentStatus.Offline,
        true, runtime.Detail, null,
        runtime.State == RuntimeState.Ready ? NotReadyCause.None : NotReadyCause.RuntimeNotPrepared, null, runtime);
}

public enum FileReadKind
{
    Ok,
    NotFound,
    Failed,
}

/// <summary>Contenu d'un fichier lu sur l'hôte — jamais journalisé tel quel (il peut s'agir du jeton).</summary>
public sealed record FileRead(FileReadKind Kind, string? Content, string Detail)
{
    public static FileRead Ok(string content) => new(FileReadKind.Ok, content, "lu");
    public static FileRead NotFound(string detail) => new(FileReadKind.NotFound, null, detail);
    public static FileRead Failed(string detail) => new(FileReadKind.Failed, null, detail);
}

/// <summary>
/// Accès en LECTURE SEULE à la machine du backend. Aucune méthode ne démarre quoi que ce soit : la
/// sonde ne fait que constater, et les lectures ne sont appelées qu'après une sonde qui a constaté
/// que l'hôte tourne déjà (voir <see cref="PanelStatusService"/>).
/// </summary>
public interface IBackendHost
{
    Task<HostProbe> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default);

    /// <summary>Lit un fichier relatif au répertoire personnel de l'utilisateur de l'hôte.</summary>
    Task<FileRead> ReadHomeFileAsync(string? distro, string relativePath, CancellationToken cancellationToken = default);

    /// <summary>Lit un fichier par chemin absolu de l'hôte (validé par l'appelant : <see cref="HostPaths.IsSafeAbsolute"/>).</summary>
    Task<FileRead> ReadFileAsync(string? distro, string absolutePath, CancellationToken cancellationToken = default);

    /// <summary>
    /// Le processus <paramref name="pid"/> est-il (encore) le backend APEX ? <c>null</c> = impossible à dire.
    /// Hôtes Linux (WSL, natif) : sa ligne de commande, lue dans <c>/proc</c>.
    /// </summary>
    async Task<bool?> BackendProcessAliveAsync(string? distro, int pid, CancellationToken cancellationToken = default)
    {
        var cmdline = await ReadFileAsync(distro, $"/proc/{pid}/cmdline", cancellationToken).ConfigureAwait(false);
        return cmdline.Kind switch
        {
            FileReadKind.Ok => (cmdline.Content ?? string.Empty).Contains("apex", StringComparison.OrdinalIgnoreCase),
            FileReadKind.NotFound => false,
            _ => null,
        };
    }
}

/// <summary>Validation des chemins reçus de l'enregistrement de découverte avant de les transmettre à <c>cat</c>.</summary>
public static class HostPaths
{
    /// <summary>
    /// Absolu (commence par « / » : jamais pris pour une option de <c>cat</c>), sans caractère de
    /// contrôle ni segment « .. ». Le chemin n'est jamais interprété par un shell (argv direct), cette
    /// validation est une défense en profondeur contre un enregistrement de découverte altéré.
    /// </summary>
    public static bool IsSafeAbsolute(string? path) => IsSafePosixAbsolute(path) || IsSafeWindowsAbsolute(path);

    /// <summary>Chemin Linux (WSL, hôte Linux) : commence par « / ».</summary>
    public static bool IsSafePosixAbsolute(string? path) =>
        !string.IsNullOrEmpty(path)
        && path.StartsWith('/')
        && path.Length < 4096
        && !path.Any(char.IsControl)
        && !path.Split('/').Contains("..");

    /// <summary>
    /// Chemin Windows complet sur un lecteur (<c>C:\Users\…</c>, mode natif) : ni chemin réseau, ni flux
    /// alternatif (« : » après le lecteur), ni caractère interdit, ni segment « .. ».
    /// </summary>
    public static bool IsSafeWindowsAbsolute(string? path) =>
        !string.IsNullOrEmpty(path)
        && path.Length is >= 3 and < 4096
        && char.IsAsciiLetter(path[0]) && path[1] == ':' && path[2] is '\\' or '/'
        && !path.Any(char.IsControl)
        && path.IndexOfAny([':', '*', '?', '"', '<', '>', '|'], 2) < 0
        && !path.Split('\\', '/').Contains("..");

    /// <summary>Relatif, sans « .. », sans caractère de contrôle, ne commençant ni par « / » ni par « - ».</summary>
    public static bool IsSafeRelative(string? path) =>
        !string.IsNullOrEmpty(path)
        && !path.StartsWith('/')
        && !path.StartsWith('-')
        && path.Length < 1024
        && !path.Any(char.IsControl)
        && !path.Split('/').Contains("..");
}

/// <summary>
/// Hôte WSL (Windows). Toutes les commandes passent par <c>wsl.exe --exec</c> (argv direct, JAMAIS le
/// shell Linux par défaut — qu'un chemin ne puisse jamais devenir une commande) ; seules
/// <c>wsl.exe -l -v</c> et <c>wsl.exe -l --running -q</c>, qui ne démarrent aucune distribution, sont
/// lancées avant de savoir si la distribution tourne. Les noms, la distribution par défaut et la version
/// WSL viennent du registre (<see cref="IWslRegistry"/>) quand il est lisible ; sinon de
/// <c>wsl.exe -l -v</c>, et seulement s'il a RÉUSSI (code 0).
/// </summary>
public sealed class WslBackendHost(IProcessRunner processRunner, IWslRegistry? registry = null) : IBackendHost
{
    private static readonly TimeSpan ProbeTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan ReadTimeout = TimeSpan.FromSeconds(10);

    public async Task<HostProbe> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default)
    {
        var result = await processRunner.RunAsync("wsl.exe", ["-l", "-v"], ProbeTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started)
        {
            // Détail court pour l'utilisateur ; la raison système complète (souvent un message .NET
            // long) n'apporte rien de plus que « wsl.exe introuvable ».
            return new HostProbe(true, ComponentStatus.Error, false,
                "WSL n'est pas installé (wsl.exe introuvable)", preferredDistro, NotReadyCause.WslNotInstalled);
        }
        if (result.TimedOut)
        {
            return new HostProbe(true, ComponentStatus.Unknown, false,
                "wsl.exe n'a pas répondu dans le délai (WSL bloqué ?)", preferredDistro, NotReadyCause.WslUnresponsive);
        }

        // L'état « en cours » vient de la liste des noms en cours (sortie non traduite), jamais de la
        // colonne ÉTAT de -l -v (traduite selon la langue de Windows). Observé sur Windows réel : code 0
        // et sortie vide quand rien ne tourne.
        var running = await processRunner.RunAsync("wsl.exe", ["-l", "--running", "-q"], ProbeTimeout, cancellationToken).ConfigureAwait(false);
        var runningOutput = running.Started && !running.TimedOut ? running.Stdout : null;

        var registered = registry?.ReadDistributions();
        var listFailed = result.ExitCode != 0;
        IReadOnlyList<WslDistroInfo> distros;
        if (registered is not null)
        {
            var runningNames = WslStatusParser.Names(runningOutput ?? string.Empty);
            distros = registered
                .Select(d => new WslDistroInfo(d.Name, d.IsDefault, runningNames.Contains(d.Name), d.Version))
                .ToList();
        }
        else
        {
            // Défaut W1 (observé sur Windows réel) : sans distribution, `wsl.exe -l -v` sort en -1 avec un
            // message de plusieurs lignes dont les mots étaient pris pour des noms de distribution. Une
            // liste en échec n'est JAMAIS analysée.
            distros = listFailed ? [] : WslStatusParser.Parse(result.Stdout, runningOutput);
        }

        var reason = Clean(result.Stderr + " " + result.Stdout);
        if (distros.Count == 0)
        {
            return new HostProbe(true, ComponentStatus.Error, false,
                "WSL est présent mais aucune distribution Linux n'est installée" + (reason.Length > 0 ? $" (wsl.exe : {Truncate(FirstLine(reason))})" : ""),
                preferredDistro, NotReadyCause.WslNotInstalled);
        }
        if (listFailed)
        {
            // Distributions enregistrées mais `wsl.exe -l -v` en échec : WSL ne répond pas correctement.
            return new HostProbe(true, ComponentStatus.Error, false,
                $"WSL ne répond pas correctement (wsl.exe -l -v : code {result.ExitCode}{(reason.Length > 0 ? " — " + Truncate(FirstLine(reason)) : "")})",
                preferredDistro, NotReadyCause.WslUnresponsive);
        }

        var selected = string.IsNullOrWhiteSpace(preferredDistro)
            ? distros.FirstOrDefault(d => d.IsDefault) ?? distros[0]
            : distros.FirstOrDefault(d => d.Name.Equals(preferredDistro, StringComparison.OrdinalIgnoreCase));
        if (selected is null)
        {
            return new HostProbe(true, ComponentStatus.Error, false,
                $"distribution « {preferredDistro} » introuvable — disponibles : {string.Join(", ", distros.Select(d => d.Name))}",
                preferredDistro, NotReadyCause.WslNotInstalled);
        }

        var runningDistros = distros.Where(d => d.IsRunning).Select(d => d.Name).ToList();
        var label = selected.Version is { } version ? $"{selected.Name} (WSL {version})" : selected.Name;
        return selected.IsRunning
            ? new HostProbe(true, ComponentStatus.Ready, true, $"{label} — en cours d'exécution", selected.Name, NotReadyCause.None, runningDistros)
            : new HostProbe(true, ComponentStatus.Offline, false, $"{label} — installée, arrêtée", selected.Name, NotReadyCause.WslStopped, runningDistros);
    }

    private static string FirstLine(string text) =>
        text.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).FirstOrDefault() ?? text;

    public Task<FileRead> ReadHomeFileAsync(string? distro, string relativePath, CancellationToken cancellationToken = default)
    {
        if (!HostPaths.IsSafeRelative(relativePath))
        {
            return Task.FromResult(FileRead.Failed("chemin relatif refusé"));
        }
        return CatAsync(BuildArgs(distro, homeDirectory: true, relativePath), cancellationToken);
    }

    public Task<FileRead> ReadFileAsync(string? distro, string absolutePath, CancellationToken cancellationToken = default)
    {
        if (!HostPaths.IsSafePosixAbsolute(absolutePath))
        {
            return Task.FromResult(FileRead.Failed("chemin absolu refusé"));
        }
        return CatAsync(BuildArgs(distro, homeDirectory: false, absolutePath), cancellationToken);
    }

    /// <summary><c>wsl.exe [-d D] [--cd ~] --exec cat -- &lt;chemin&gt;</c> : argv direct, aucun shell.</summary>
    internal static List<string> BuildArgs(string? distro, bool homeDirectory, string path)
    {
        var args = new List<string>();
        if (!string.IsNullOrWhiteSpace(distro))
        {
            args.Add("-d");
            args.Add(distro);
        }
        if (homeDirectory)
        {
            args.Add("--cd");
            args.Add("~");
        }
        args.AddRange(["--exec", "cat", "--", path]);
        return args;
    }

    private async Task<FileRead> CatAsync(List<string> args, CancellationToken cancellationToken)
    {
        var result = await processRunner.RunAsync("wsl.exe", args, ReadTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started)
        {
            return FileRead.Failed($"wsl.exe n'a pas pu être lancé ({result.FailureReason})");
        }
        if (result.TimedOut)
        {
            return FileRead.Failed("lecture dans WSL sans réponse dans le délai");
        }
        if (result.ExitCode != 0)
        {
            var stderr = Clean(result.Stderr);
            return stderr.Contains("No such file", StringComparison.OrdinalIgnoreCase)
                ? FileRead.NotFound("fichier absent")
                : FileRead.Failed($"lecture refusée (code {result.ExitCode}) : {Truncate(stderr)}");
        }
        return FileRead.Ok(result.Stdout);
    }

    private static string Clean(string text) => text.Replace("\0", string.Empty).Trim();

    private static string Truncate(string text) => text.Length <= 200 ? text : text[..200] + "…";
}

/// <summary>Hôte Linux natif : le backend tourne sur la même machine que le Panel ; lectures directes du système de fichiers.</summary>
public sealed class LocalBackendHost(string? homeDirectory = null) : IBackendHost
{
    private readonly string _home = homeDirectory ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);

    public Task<HostProbe> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default) =>
        Task.FromResult(HostProbe.LocalHost());

    public Task<FileRead> ReadHomeFileAsync(string? distro, string relativePath, CancellationToken cancellationToken = default) =>
        HostPaths.IsSafeRelative(relativePath)
            ? ReadAsync(Path.Combine(_home, relativePath), cancellationToken)
            : Task.FromResult(FileRead.Failed("chemin relatif refusé"));

    public Task<FileRead> ReadFileAsync(string? distro, string absolutePath, CancellationToken cancellationToken = default) =>
        HostPaths.IsSafePosixAbsolute(absolutePath)
            ? ReadAsync(absolutePath, cancellationToken)
            : Task.FromResult(FileRead.Failed("chemin absolu refusé"));

    internal static async Task<FileRead> ReadAsync(string path, CancellationToken cancellationToken)
    {
        try
        {
            return FileRead.Ok(await File.ReadAllTextAsync(path, cancellationToken).ConfigureAwait(false));
        }
        catch (Exception ex) when (ex is FileNotFoundException or DirectoryNotFoundException)
        {
            return FileRead.NotFound("fichier absent");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return FileRead.Failed($"lecture refusée : {ex.Message}");
        }
    }
}

/// <summary>
/// APEX AUTONOME sur ce poste (mode « Windows natif ») : le backend est le Python d'APEX lancé par le Panel, ses
/// fichiers sont sous <c>APEX_HOME</c> (<c>%LOCALAPPDATA%\APEX</c>) aux mêmes chemins relatifs que dans WSL
/// (<c>.apex\runtime\backend.json</c>, <c>.apex\api-token</c>). Lectures directes, rien n'est démarré.
/// </summary>
public sealed class NativeBackendHost(NativeRuntime runtime) : IBackendHost
{
    public NativeRuntime Runtime => runtime;

    public Task<HostProbe> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default) =>
        Task.FromResult(HostProbe.Native(runtime.Inspect()));

    public Task<FileRead> ReadHomeFileAsync(string? distro, string relativePath, CancellationToken cancellationToken = default) =>
        HostPaths.IsSafeRelative(relativePath)
            ? LocalBackendHost.ReadAsync(Path.Combine(runtime.Home, relativePath), cancellationToken)
            : Task.FromResult(FileRead.Failed("chemin relatif refusé"));

    public Task<FileRead> ReadFileAsync(string? distro, string absolutePath, CancellationToken cancellationToken = default) =>
        (OperatingSystem.IsWindows() ? HostPaths.IsSafeWindowsAbsolute(absolutePath) : HostPaths.IsSafePosixAbsolute(absolutePath))
            ? LocalBackendHost.ReadAsync(absolutePath, cancellationToken)
            : Task.FromResult(FileRead.Failed("chemin absolu refusé"));

    /// <summary>Vivant ET porté par le Python d'APEX (un pid réutilisé par un autre programme n'est pas APEX).</summary>
    public Task<bool?> BackendProcessAliveAsync(string? distro, int pid, CancellationToken cancellationToken = default)
    {
        var spec = runtime.TryReadSpec(out _);
        return Task.FromResult(IsProcessRunning(pid, runtime.PythonExecutable(spec)));
    }

    internal static bool? IsProcessRunning(int pid, string expectedExecutable)
    {
        try
        {
            using var process = System.Diagnostics.Process.GetProcessById(pid);
            if (process.HasExited)
            {
                return false;
            }
            var image = process.MainModule?.FileName;
            return image is null ? null : SameFile(image, expectedExecutable);
        }
        catch (ArgumentException)
        {
            return false; // aucun processus de ce pid
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException or NotSupportedException)
        {
            return null; // existe peut-être, mais illisible : on ne conclut pas
        }
    }

    internal static bool SameFile(string a, string b)
    {
        static string Resolve(string path)
        {
            try
            {
                var full = Path.GetFullPath(path);
                return File.ResolveLinkTarget(full, returnFinalTarget: true)?.FullName ?? full;
            }
            catch (IOException)
            {
                return path;
            }
        }
        return string.Equals(Resolve(a), Resolve(b), OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal);
    }
}
