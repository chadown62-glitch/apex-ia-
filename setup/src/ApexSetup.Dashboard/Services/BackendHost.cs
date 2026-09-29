using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>État de la machine qui exécute le backend (distribution WSL sous Windows ; la machine elle-même sous Linux).</summary>
/// <param name="Applicable">Faux sur un hôte Linux natif : il n'y a pas de WSL, la ligne n'est pas affichée.</param>
/// <param name="Status">État de WSL (sans objet si non applicable).</param>
/// <param name="Running">Vrai si l'on peut lire des fichiers de l'hôte SANS le démarrer.</param>
public sealed record HostProbe(bool Applicable, ComponentStatus Status, bool Running, string Detail, string? Distro, NotReadyCause Cause)
{
    public static HostProbe LocalHost() => new(false, ComponentStatus.Ready, true, "hôte Linux natif (sans WSL)", null, NotReadyCause.None);
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
}

/// <summary>Validation des chemins reçus de l'enregistrement de découverte avant de les transmettre à <c>cat</c>.</summary>
public static class HostPaths
{
    /// <summary>
    /// Absolu (commence par « / » : jamais pris pour une option de <c>cat</c>), sans caractère de
    /// contrôle ni segment « .. ». Le chemin n'est jamais interprété par un shell (argv direct), cette
    /// validation est une défense en profondeur contre un enregistrement de découverte altéré.
    /// </summary>
    public static bool IsSafeAbsolute(string? path) =>
        !string.IsNullOrEmpty(path)
        && path.StartsWith('/')
        && path.Length < 4096
        && !path.Any(char.IsControl)
        && !path.Split('/').Contains("..");

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
/// shell Linux par défaut — qu'un chemin ne puisse jamais devenir une commande) ; seule
/// <c>wsl.exe -l -v</c>, qui ne démarre aucune distribution, est lancée avant de savoir si la
/// distribution tourne.
/// </summary>
public sealed class WslBackendHost(IProcessRunner processRunner) : IBackendHost
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

        var distros = WslStatusParser.Parse(result.Stdout);
        if (distros.Count == 0)
        {
            var reason = Clean(result.Stderr + " " + result.Stdout);
            return new HostProbe(true, ComponentStatus.Error, false,
                $"WSL est présent mais aucune distribution Linux n'est installée ({reason})", preferredDistro, NotReadyCause.WslNotInstalled);
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

        return selected.IsRunning
            ? new HostProbe(true, ComponentStatus.Ready, true, $"{selected.Name} — en cours d'exécution", selected.Name, NotReadyCause.None)
            : new HostProbe(true, ComponentStatus.Offline, false, $"{selected.Name} — installée, arrêtée", selected.Name, NotReadyCause.WslStopped);
    }

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
        if (!HostPaths.IsSafeAbsolute(absolutePath))
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
        HostPaths.IsSafeAbsolute(absolutePath)
            ? ReadAsync(absolutePath, cancellationToken)
            : Task.FromResult(FileRead.Failed("chemin absolu refusé"));

    private static async Task<FileRead> ReadAsync(string path, CancellationToken cancellationToken)
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
