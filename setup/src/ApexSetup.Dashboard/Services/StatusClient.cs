using System.Net;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text.Json;

namespace ApexSetup.Dashboard.Services;

public enum FetchOutcome
{
    NotAttempted,
    Ok,
    Refused,
    Timeout,
    Unauthorized,
    HttpError,
    /// <summary>Un programme répond sur le port mais ce n'est pas l'API APEX.</summary>
    NotApex,
    /// <summary>Une API APEX répond mais sans <c>/api/v1/status</c> : backend antérieur au Panel.</summary>
    OldBackend,
    Failed,
}

/// <summary>Un service tel que MESURÉ par le backend.</summary>
public sealed record BackendComponent(string Name, string State, string Detail);

/// <summary>Contenu utile de <c>GET /api/v1/status</c>.</summary>
public sealed record StatusPayload(
    string ApexVersion, string ApiVersion, int PanelProtocol, int? Pid, IReadOnlyList<BackendComponent> Components);

public sealed record StatusFetch(FetchOutcome Outcome, StatusPayload? Payload, int? HttpStatus, string Detail)
{
    public static StatusFetch NotAttempted(string detail) => new(FetchOutcome.NotAttempted, null, null, detail);
}

/// <summary>
/// Client de <c>GET /api/v1/status</c> : une vraie requête, dont chaque issue (refus, délai, 401,
/// erreur HTTP, autre programme, ancien backend, réponse valide) est distinguée. Le jeton est posé
/// dans l'en-tête et n'apparaît jamais dans un détail.
/// </summary>
public sealed class StatusClient(HttpClient httpClient)
{
    public static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(10);

    public async Task<StatusFetch> FetchAsync(
        string host, int port, string? token, TimeSpan? timeout = null, CancellationToken cancellationToken = default)
    {
        var limit = timeout ?? DefaultTimeout;
        var baseUrl = $"http://{(host.Contains(':') ? $"[{host}]" : host)}:{port}";
        var (outcome, status, body, detail) = await GetAsync($"{baseUrl}/api/v1/status", token, limit, cancellationToken).ConfigureAwait(false);
        switch (outcome)
        {
            case FetchOutcome.Ok:
                return Parse(body!, status!.Value);
            case FetchOutcome.HttpError when status == 401:
                return new StatusFetch(FetchOutcome.Unauthorized, null, 401,
                    token is null ? "l'API exige un jeton et aucun n'a été trouvé" : "jeton refusé par l'API (401)");
            case FetchOutcome.HttpError when status == 404:
                // Route inconnue : un backend APEX antérieur (sans /status) protège /health par le jeton
                // (401 JSON) ; un autre programme ne connaît ni l'un ni l'autre.
                var (healthOutcome, healthStatus, healthBody, _) =
                    await GetAsync($"{baseUrl}/api/v1/health", null, limit, cancellationToken).ConfigureAwait(false);
                var apexLike = healthStatus is 401 or 200 && LooksLikeFastApi(healthBody);
                return apexLike && healthOutcome is FetchOutcome.Ok or FetchOutcome.HttpError
                    ? new StatusFetch(FetchOutcome.OldBackend, null, 404, "API APEX sans /api/v1/status : backend antérieur à ce Panel")
                    : new StatusFetch(FetchOutcome.NotApex, null, 404, $"un autre programme répond sur le port {port}");
            case FetchOutcome.HttpError:
                return new StatusFetch(FetchOutcome.HttpError, null, status, $"l'API répond HTTP {status}");
            default:
                return new StatusFetch(outcome, null, status, detail);
        }
    }

    private static bool LooksLikeFastApi(string? body)
    {
        if (string.IsNullOrEmpty(body))
        {
            return false;
        }
        try
        {
            using var doc = JsonDocument.Parse(body);
            return doc.RootElement.ValueKind == JsonValueKind.Object
                && (doc.RootElement.TryGetProperty("detail", out _) || doc.RootElement.TryGetProperty("status", out _));
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private async Task<(FetchOutcome Outcome, int? Status, string? Body, string Detail)> GetAsync(
        string url, string? token, TimeSpan timeout, CancellationToken cancellationToken)
    {
        HttpRequestMessage built;
        try
        {
            built = new HttpRequestMessage(HttpMethod.Get, url);
        }
        catch (FormatException ex)
        {
            // Hôte altéré dans l'enregistrement de découverte : jamais une exception jusqu'à l'interface.
            return (FetchOutcome.Failed, null, null, $"adresse invalide : {ex.Message}");
        }
        using var request = built;
        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        cts.CancelAfter(timeout);
        try
        {
            using var response = await httpClient.SendAsync(request, cts.Token).ConfigureAwait(false);
            var body = await response.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
            var code = (int)response.StatusCode;
            return response.IsSuccessStatusCode
                ? (FetchOutcome.Ok, code, body, $"HTTP {code}")
                : (FetchOutcome.HttpError, code, body, $"HTTP {code}");
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return (FetchOutcome.Timeout, null, null, $"aucune réponse sous {timeout.TotalSeconds:0}s");
        }
        catch (HttpRequestException ex)
        {
            var refused = ex.InnerException is SocketException { SocketErrorCode: SocketError.ConnectionRefused }
                || ex.HttpRequestError == HttpRequestError.ConnectionError;
            return refused
                ? (FetchOutcome.Refused, null, null, "connexion refusée : rien n'écoute sur ce port")
                : (FetchOutcome.Failed, null, null, ex.Message);
        }
    }

    /// <summary>Analyse stricte : toute réponse qui n'a pas exactement la forme attendue est « pas APEX », jamais lue partiellement.</summary>
    internal static StatusFetch Parse(string body, int httpStatus)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            var envelope = doc.RootElement;
            if (!envelope.TryGetProperty("data", out var data) || data.ValueKind != JsonValueKind.Object
                || !data.TryGetProperty("service", out var service) || service.GetString() != "apex")
            {
                return new StatusFetch(FetchOutcome.NotApex, null, httpStatus, "réponse qui n'est pas celle de l'API APEX");
            }
            if (!data.TryGetProperty("versions", out var versions) || versions.ValueKind != JsonValueKind.Object
                || !versions.TryGetProperty("panel_protocol", out var protocol) || !protocol.TryGetInt32(out var panelProtocol))
            {
                return new StatusFetch(FetchOutcome.OldBackend, null, httpStatus, "réponse APEX sans version de protocole");
            }
            int? pid = data.TryGetProperty("backend", out var backend) && backend.ValueKind == JsonValueKind.Object
                && backend.TryGetProperty("pid", out var pidValue) && pidValue.TryGetInt32(out var pidNumber)
                ? pidNumber
                : null;
            var components = new List<BackendComponent>();
            if (data.TryGetProperty("components", out var list) && list.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in list.EnumerateArray())
                {
                    if (item.ValueKind == JsonValueKind.Object
                        && item.TryGetProperty("component", out var name) && name.ValueKind == JsonValueKind.String
                        && item.TryGetProperty("state", out var state) && state.ValueKind == JsonValueKind.String)
                    {
                        var detail = item.TryGetProperty("detail", out var d) && d.ValueKind == JsonValueKind.String ? d.GetString() : null;
                        components.Add(new BackendComponent(name.GetString()!, state.GetString()!, detail ?? string.Empty));
                    }
                }
            }
            var payload = new StatusPayload(
                versions.TryGetProperty("apex", out var apex) ? apex.GetString() ?? "?" : "?",
                versions.TryGetProperty("api", out var api) ? api.GetString() ?? "?" : "?",
                panelProtocol, pid, components);
            return new StatusFetch(FetchOutcome.Ok, payload, httpStatus, $"HTTP {httpStatus}");
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException)
        {
            return new StatusFetch(FetchOutcome.NotApex, null, httpStatus, "réponse non JSON : ce n'est pas l'API APEX");
        }
    }
}
