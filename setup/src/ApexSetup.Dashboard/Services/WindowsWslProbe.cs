using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

public sealed class WindowsWslProbe(IProcessRunner processRunner) : IWslProbe
{
    private static readonly TimeSpan ProbeTimeout = TimeSpan.FromSeconds(10);

    public async Task<ComponentState> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default)
    {
        var result = await processRunner.RunAsync("wsl.exe", ["-l", "-v"], ProbeTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started)
        {
            return new ComponentState(
                ComponentKey.Wsl, "WSL", ComponentStatus.NotInstalled,
                $"wsl.exe introuvable — WSL2 n'est pas installé ou hors PATH ({result.FailureReason})");
        }

        if (result.TimedOut)
        {
            return new ComponentState(ComponentKey.Wsl, "WSL", ComponentStatus.Unknown, "wsl.exe n'a pas répondu dans le délai imparti");
        }

        var distros = WslStatusParser.Parse(result.Stdout);
        if (distros.Count == 0)
        {
            return new ComponentState(
                ComponentKey.Wsl, "WSL", ComponentStatus.NotInstalled,
                $"WSL2 est présent mais aucune distribution n'est installée ({result.Stderr.Trim()})");
        }

        var selected = string.IsNullOrWhiteSpace(preferredDistro)
            ? distros.FirstOrDefault(d => d.IsDefault) ?? distros[0]
            : distros.FirstOrDefault(d => d.Name.Equals(preferredDistro, StringComparison.OrdinalIgnoreCase));

        if (selected is null)
        {
            return new ComponentState(
                ComponentKey.Wsl, "WSL", ComponentStatus.Error,
                $"distribution « {preferredDistro} » introuvable — disponibles : {string.Join(", ", distros.Select(d => d.Name))}");
        }

        return selected.IsRunning
            ? new ComponentState(ComponentKey.Wsl, "WSL", ComponentStatus.Running, $"{selected.Name} — en cours d'exécution")
            : new ComponentState(ComponentKey.Wsl, "WSL", ComponentStatus.Stopped, $"{selected.Name} — installée, pas encore démarrée");
    }

    public async Task<bool> PathExistsAsync(string? distro, string wslPath, CancellationToken cancellationToken = default)
    {
        var args = BuildDistroArgs(distro);
        args.AddRange(["--", "test", "-d", wslPath]);
        var result = await processRunner.RunAsync("wsl.exe", args, ProbeTimeout, cancellationToken).ConfigureAwait(false);
        return result.Started && !result.TimedOut && result.ExitCode == 0;
    }

    internal static List<string> BuildDistroArgs(string? distro)
    {
        var args = new List<string>();
        if (!string.IsNullOrWhiteSpace(distro))
        {
            args.Add("-d");
            args.Add(distro);
        }
        return args;
    }
}
