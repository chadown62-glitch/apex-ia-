using System.Text.Json;
using ApexSetup.Dashboard.Models;
using ApexSetup.Dashboard.Services;

namespace ApexSetup.Dashboard.Tests.Fixtures;

/// <summary>Hôte scriptable qui ENREGISTRE chaque lecture — pour prouver qu'aucune n'a lieu quand l'hôte est arrêté.</summary>
public sealed class FakeBackendHost : IBackendHost
{
    public HostProbe Probe { get; set; } = HostProbe.LocalHost();
    public Dictionary<string, FileRead> Files { get; } = new();
    public List<string> Reads { get; } = [];

    /// <summary>Attendu au début de chaque mesure : simule une mesure lente (Windows réel : 6 s).</summary>
    public Func<Task>? BeforeProbe { get; set; }

    public async Task<HostProbe> ProbeAsync(string? preferredDistro, CancellationToken cancellationToken = default)
    {
        if (BeforeProbe is { } wait)
        {
            await wait();
        }
        return Probe;
    }

    public Task<FileRead> ReadHomeFileAsync(string? distro, string relativePath, CancellationToken cancellationToken = default) =>
        Read("~/" + relativePath);

    public Task<FileRead> ReadFileAsync(string? distro, string absolutePath, CancellationToken cancellationToken = default) =>
        Read(absolutePath);

    private Task<FileRead> Read(string path)
    {
        Reads.Add(path);
        return Task.FromResult(Files.TryGetValue(path, out var read) ? read : FileRead.NotFound("absent"));
    }
}

public static class PanelData
{
    public const string ProjectRoot = "/home/utilisateur/apex";
    public const string Token = "tok_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_";

    public static readonly DateTimeOffset Now = new(2026, 9, 29, 12, 0, 0, TimeSpan.Zero);

    public static string DiscoveryJson(int port = 8000, int pid = 4242, int protocol = 1, DateTimeOffset? startedAt = null, string? phase = "serving") =>
        JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["schema"] = 1,
            ["service"] = "apex",
            ["apex_version"] = "0.1.2",
            ["api_version"] = "v1",
            ["panel_protocol"] = protocol,
            ["project_root"] = ProjectRoot,
            ["token_file"] = ".apex/api-token",
            ["host"] = "127.0.0.1",
            ["port"] = port,
            ["pid"] = pid,
            ["started_at"] = (startedAt ?? Now.AddMinutes(-5)).ToString("o"),
        }.Concat(phase is null ? [] : [new KeyValuePair<string, object>("phase", phase)]).ToDictionary());

    public static DiscoveryRead Discovery(DateTimeOffset? startedAt = null, string? phase = "serving") =>
        BackendDiscovery.Parse(DiscoveryJson(startedAt: startedAt, phase: phase));

    public static TokenRead GoodToken() => TokenRead.FromFile(FileRead.Ok(Token + "\n"));

    /// <summary>Réponse de <c>GET /api/v1/status</c> au format exact du backend (apex/api/routes/status.py).</summary>
    public static string StatusJson(int protocol = 1, IDictionary<string, string>? states = null, string service = "apex")
    {
        states ??= ComponentKeys.BackendServices.ToDictionary(s => s.BackendName, _ => "ready");
        var components = new List<object> { new { component = "api", state = "ready", detail = "requête authentifiée servie", latency_ms = (double?)null } };
        components.AddRange(states.Select(kv => new { component = kv.Key, state = kv.Value, detail = $"{kv.Key} mesuré", latency_ms = (double?)1.5 }));
        return JsonSerializer.Serialize(new
        {
            status = "real",
            data = new
            {
                service,
                versions = new { apex = "0.1.2", api = "v1", panel_protocol = protocol },
                backend = new { pid = 4242, started_at = Now.AddMinutes(-5).ToString("o") },
                components,
                measured_at = Now.ToString("o"),
            },
        });
    }

    public static StatusFetch Fetch(string json) => StatusClient.Parse(json, 200);

    public static HostProbe WslRunning() => new(true, ComponentStatus.Ready, true, "Ubuntu — en cours d'exécution", "Ubuntu", NotReadyCause.None);

    public static HostProbe WslStopped() => new(true, ComponentStatus.Offline, false, "Ubuntu — installée, arrêtée", "Ubuntu", NotReadyCause.WslStopped);

    public static HostProbe WslMissing() => new(true, ComponentStatus.Error, false, "WSL n'est pas installé", null, NotReadyCause.WslNotInstalled);

    public static PanelSnapshot Decide(
        HostProbe host, DiscoveryRead discovery, StatusFetch fetch, TokenRead? token = null, bool? alive = null, int port = 8000) =>
        StatusEngine.Decide(new StatusInputs(host, discovery, alive, token ?? TokenRead.NotRead, fetch, port, Now, TimeSpan.FromMilliseconds(12)), "0.2.0");
}
