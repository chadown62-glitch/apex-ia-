using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

public sealed class WebPanelChecker(HttpClient httpClient) : IWebPanelChecker
{
    private static readonly TimeSpan HttpTimeout = TimeSpan.FromSeconds(5);

    public async Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        var probe = await HttpProber.ProbeAsync(
            httpClient, settings.WebPanelUrl, bearerToken: null, HttpTimeout, cancellationToken).ConfigureAwait(false);

        var status = probe.Outcome switch
        {
            HttpProbeOutcome.Ok => ComponentStatus.Running,
            HttpProbeOutcome.ConnectionRefused => ComponentStatus.Stopped,
            HttpProbeOutcome.Timeout => ComponentStatus.Error,
            HttpProbeOutcome.HttpError => ComponentStatus.Error,
            _ => ComponentStatus.Unknown,
        };
        return new ComponentState(ComponentKey.WebPanel, "Web Panel", status, probe.Detail);
    }
}
