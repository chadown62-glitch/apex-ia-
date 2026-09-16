using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

public sealed class ApiHealthChecker(IProcessRunner processRunner, HttpClient httpClient) : IApiHealthChecker
{
    private static readonly TimeSpan TokenReadTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan HttpTimeout = TimeSpan.FromSeconds(5);

    public async Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(settings.ProjectPathWsl))
        {
            return ComponentState.Unknown(ComponentKey.Api, "API APEX", "chemin du projet WSL non configuré — voir Paramètres");
        }

        var token = await ReadApiTokenAsync(settings, cancellationToken).ConfigureAwait(false);
        if (token is null)
        {
            return new ComponentState(
                ComponentKey.Api, "API APEX", ComponentStatus.Unknown,
                "jeton API illisible (.apex/api-token) — l'API n'a peut-être jamais démarré");
        }

        var probe = await HttpProber.ProbeAsync(
            httpClient, $"{settings.ApiBaseUrl}/health", token, HttpTimeout, cancellationToken).ConfigureAwait(false);

        var status = probe.Outcome switch
        {
            HttpProbeOutcome.Ok => ComponentStatus.Running,
            HttpProbeOutcome.ConnectionRefused => ComponentStatus.Stopped,
            HttpProbeOutcome.Timeout => ComponentStatus.Error,
            HttpProbeOutcome.HttpError => ComponentStatus.Error,
            _ => ComponentStatus.Unknown,
        };
        return new ComponentState(ComponentKey.Api, "API APEX", status, probe.Detail);
    }

    private async Task<string?> ReadApiTokenAsync(DashboardSettings settings, CancellationToken cancellationToken)
    {
        var args = WindowsWslProbe.BuildDistroArgs(settings.WslDistro);
        args.AddRange(["--", "cat", $"{settings.ProjectPathWsl}/.apex/api-token"]);
        var result = await processRunner.RunAsync("wsl.exe", args, TokenReadTimeout, cancellationToken).ConfigureAwait(false);
        if (!result.Started || result.TimedOut || result.ExitCode != 0)
        {
            return null;
        }
        var token = result.Stdout.Trim();
        return token.Length == 0 ? null : token;
    }
}
