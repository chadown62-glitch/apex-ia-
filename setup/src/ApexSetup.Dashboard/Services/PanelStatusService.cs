using System.Diagnostics;
using ApexSetup.Dashboard.Models;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Une vérification complète, dans cet ordre, en lecture seule :
/// <list type="number">
/// <item>état de l'hôte (<c>wsl.exe -l -v</c> sous Windows) — si la distribution est arrêtée, on
/// s'arrête là : aucune commande qui la démarrerait ;</item>
/// <item>enregistrement de découverte (<c>~/.apex/runtime/backend.json</c>) ;</item>
/// <item>processus du backend vivant ? (<c>/proc/&lt;pid&gt;/cmdline</c>) ;</item>
/// <item>jeton (<c>&lt;projet&gt;/.apex/api-token</c>), en mémoire seulement ;</item>
/// <item><c>GET /api/v1/status</c> sur le port découvert (ou 8000).</item>
/// </list>
/// </summary>
/// <summary>Accès au backend qui a RÉPONDU avec le jeton lu dans son dossier — seul point de départ de « Ouvrir APEX ».</summary>
public sealed record BackendAccess(string Address, int Port, string Token, string? ProjectRoot, string? Distro)
{
    public override string ToString() => $"BackendAccess({Address}:{Port}, {ProjectRoot})"; // jamais le jeton
}

public sealed class PanelStatusService(IBackendHost host, StatusClient client, Func<DateTimeOffset>? clock = null)
{
    /// <summary>Renseigné seulement après une réponse authentifiée au bon protocole ; <c>null</c> sinon.</summary>
    public BackendAccess? LastAccess { get; private set; }

    /// <summary>Distribution où le backend a été cherché à la dernière mesure (journal du démarrage).</summary>
    public string? LastDistro { get; private set; }

    /// <summary>Journal du dernier démarrage (<c>~/.apex/runtime/launch.log</c>) — lecture seule, comme le reste.</summary>
    public Task<FileRead> ReadLaunchLogAsync(DashboardSettings settings, CancellationToken cancellationToken = default) =>
        host.ReadHomeFileAsync(LastDistro ?? (string.IsNullOrWhiteSpace(settings.WslDistro) ? null : settings.WslDistro.Trim()),
            BackendLauncher.LaunchLog, cancellationToken);

    public HostKind HostKind => host is LocalBackendHost ? HostKind.Local : HostKind.Wsl;

    private readonly Func<DateTimeOffset> _clock = clock ?? (() => DateTimeOffset.Now);

    /// <summary>Appelé avec chaque jeton lu, pour que les journaux puissent le masquer s'il y apparaissait.</summary>
    public event Action<string>? SecretRead;

    public async Task<PanelSnapshot> MeasureAsync(
        DashboardSettings settings, CancellationToken cancellationToken = default, LaunchAttempt? launch = null)
    {
        _launch = launch;
        LastAccess = null;
        var watch = Stopwatch.StartNew();
        var distro = string.IsNullOrWhiteSpace(settings.WslDistro) ? null : settings.WslDistro.Trim();
        var probe = await host.ProbeAsync(distro, cancellationToken).ConfigureAwait(false);
        var port = settings.ApiPort ?? DashboardSettings.DefaultApiPort;

        // Où chercher le backend, sans jamais démarrer une distribution : la distribution choisie (ou par
        // défaut) si elle tourne, puis — si l'utilisateur n'en a choisi aucune — les AUTRES distributions
        // déjà en cours (launch.sh lancé ailleurs que dans la distribution par défaut).
        var candidates = new List<string?>();
        if (probe.Running)
        {
            candidates.Add(probe.Distro ?? distro);
        }
        if (distro is null)
        {
            candidates.AddRange((probe.RunningDistros ?? []).Where(d => d != probe.Distro));
        }
        if (candidates.Count == 0)
        {
            return Decide(probe, DiscoveryRead.NotRead, null, TokenRead.NotRead,
                StatusFetch.NotAttempted("non tentée : hôte arrêté"), port, watch);
        }

        var distroForReads = candidates[0];
        LastDistro = distroForReads;
        DiscoveryRead? firstRead = null;
        DiscoveryRead? foundRead = null;
        foreach (var candidate in candidates)
        {
            var read = BackendDiscovery.FromFile(
                await host.ReadHomeFileAsync(candidate, BackendDiscovery.RelativePath, cancellationToken).ConfigureAwait(false));
            firstRead ??= read;
            if (read.Kind is DiscoveryKind.Found or DiscoveryKind.Invalid)
            {
                (distroForReads, foundRead) = (candidate, read);
                LastDistro = candidate;
                break;
            }
        }
        if (foundRead is null && !probe.Running)
        {
            // Aucun backend ailleurs, distribution choisie arrêtée : verdict « WSL arrêté », rien d'autre lu.
            return Decide(probe, DiscoveryRead.NotRead, null, TokenRead.NotRead,
                StatusFetch.NotAttempted("non tentée : hôte arrêté"), port, watch);
        }
        var discovery = foundRead ?? firstRead ?? DiscoveryRead.NotRead;
        if (foundRead is not null && probe.Applicable && distroForReads != probe.Distro)
        {
            probe = probe with
            {
                Status = ComponentStatus.Ready,
                Running = true,
                Distro = distroForReads,
                Cause = NotReadyCause.None,
                Detail = $"{distroForReads} — en cours d'exécution (backend trouvé dans cette distribution ; par défaut : {probe.Distro})",
            };
        }
        bool? alive = null;
        var token = TokenRead.NotRead;
        var address = "127.0.0.1";
        if (discovery.Record is { } record)
        {
            port = record.Port;
            address = BackendDiscovery.ReachableHost(record.Host);
            var cmdline = await host.ReadFileAsync(distroForReads, $"/proc/{record.Pid}/cmdline", cancellationToken).ConfigureAwait(false);
            alive = cmdline.Kind switch
            {
                FileReadKind.Ok => (cmdline.Content ?? string.Empty).Contains("apex", StringComparison.OrdinalIgnoreCase),
                FileReadKind.NotFound => false,
                _ => null,
            };
            token = TokenRead.FromFile(
                await host.ReadFileAsync(distroForReads, BackendDiscovery.TokenPath(record), cancellationToken).ConfigureAwait(false));
            if (token.Value is { } secret)
            {
                SecretRead?.Invoke(secret);
            }
        }

        var fetch = await client.FetchAsync(address, port, token.Value, cancellationToken: cancellationToken).ConfigureAwait(false);
        if (fetch.Outcome == FetchOutcome.Ok && fetch.Payload?.PanelProtocol == StatusEngine.SupportedProtocol
            && token.Value is { } verified && discovery.Record is { } served)
        {
            LastAccess = new BackendAccess(address, port, verified, served.ProjectRoot, distroForReads);
        }
        bool? listening = null;
        if (fetch.Outcome == FetchOutcome.Refused && probe.Applicable && alive == true)
        {
            // Refus vu de Windows alors que le processus vit : écoute-t-il DANS la distribution ?
            listening = false;
            foreach (var table in new[] { "/proc/net/tcp", "/proc/net/tcp6" })
            {
                var read = await host.ReadFileAsync(distroForReads, table, cancellationToken).ConfigureAwait(false);
                if (read.Kind == FileReadKind.NotFound && table.EndsWith('6'))
                {
                    continue; // IPv6 désactivé dans la distribution : pas de table tcp6 (constaté)
                }
                if (read.Kind != FileReadKind.Ok)
                {
                    listening = null; // illisible : on ne conclut pas
                    break;
                }
                if (ProcNetTcp.IsListening(read.Content, port))
                {
                    listening = true;
                    break;
                }
            }
        }
        return Decide(probe, discovery, alive, token, fetch, port, watch, listening);
    }

    private LaunchAttempt? _launch;

    private PanelSnapshot Decide(
        HostProbe probe, DiscoveryRead discovery, bool? alive, TokenRead token, StatusFetch fetch, int port, Stopwatch watch,
        bool? listening = null) =>
        StatusEngine.Decide(new StatusInputs(probe, discovery, alive, token, fetch, port, _clock(), watch.Elapsed, listening, _launch),
            PanelInfo.Version);
}
