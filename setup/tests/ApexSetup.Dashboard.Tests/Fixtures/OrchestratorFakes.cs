using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.Tests.Fixtures;

public sealed class FakeWslProbe : IWslProbe
{
    public ComponentState ProbeResult { get; set; } = new(ComponentKey.Wsl, "WSL", ComponentStatus.Running, "Ubuntu — en cours d'exécution");
    public bool PathExists { get; set; } = true;

    public Task<ComponentState> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default) =>
        Task.FromResult(ProbeResult);

    public Task<bool> PathExistsAsync(string? distro, string wslPath, CancellationToken cancellationToken = default) =>
        Task.FromResult(PathExists);
}

public sealed class FakeRuntimeStatusService : IRuntimeStatusService
{
    public IReadOnlyList<ComponentState> States { get; set; } = [];

    public Task<IReadOnlyList<ComponentState>> GetComponentStatesAsync(CancellationToken cancellationToken = default) =>
        Task.FromResult(States);
}

/// <summary>Retourne une SÉQUENCE de résultats (un par appel) — permet à un test d'observer une transition réelle Stopped -> Running après N sondages, sans jamais dépendre d'un vrai délai.</summary>
public sealed class SequencedHealthChecker : IApiHealthChecker, IWebPanelChecker
{
    private readonly Queue<ComponentState> _sequence;
    private readonly ComponentState _final;
    public int CallCount { get; private set; }

    public SequencedHealthChecker(IEnumerable<ComponentState> sequence, ComponentState final)
    {
        _sequence = new Queue<ComponentState>(sequence);
        _final = final;
    }

    public Task<ComponentState> CheckAsync(DashboardSettings settings, CancellationToken cancellationToken = default)
    {
        CallCount++;
        return Task.FromResult(_sequence.Count > 0 ? _sequence.Dequeue() : _final);
    }
}

public sealed class FakeBrowserLauncher : IBrowserLauncher
{
    public string? OpenedUrl { get; private set; }

    /// <summary>Simule un échec réel d'ouverture (ex. aucun navigateur par défaut enregistré) — voir <see cref="Open"/>.</summary>
    public bool OpenSucceeds { get; set; } = true;

    public bool Open(string url)
    {
        OpenedUrl = url;
        return OpenSucceeds;
    }
}
