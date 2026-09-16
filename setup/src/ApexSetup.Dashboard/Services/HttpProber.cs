using System.Net;
using System.Net.Sockets;

namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Requête HTTP RÉELLE et sa classification — mission §10/§11 : jamais un
/// port ouvert ou un processus lancé interprété comme "disponible" ; une
/// vraie requête <see cref="HttpMethod.Get"/> est toujours effectuée, et son
/// résultat (succès, refus de connexion, timeout, code d'erreur) est
/// distingué explicitement, jamais réduit à un booléen.
/// </summary>
public static class HttpProber
{
    public static async Task<HttpProbeResult> ProbeAsync(
        HttpClient httpClient, string url, string? bearerToken, TimeSpan timeout, CancellationToken cancellationToken = default)
    {
        // BUG RÉEL trouvé par revue de code (validation physique N+31) :
        // une URL construite depuis une configuration invalide (ex.
        // `ApiHost` vidé dans les Paramètres -> "http://:8000/health")
        // fait lever `UriFormatException` par le constructeur de
        // `HttpRequestMessage` — vérifié empiriquement (`new
        // HttpRequestMessage(Get, "http://:8000/health")` lève bien
        // `System.UriFormatException: Invalid URI: The hostname could not
        // be parsed.`). Cette exception n'était PAS interceptée par les
        // `catch` existants (`OperationCanceledException`/
        // `HttpRequestException` seulement) et remontait donc jusqu'au
        // ViewModel — un crash réel pour le cas "configuration invalide"
        // explicitement exigé testé par la mission. `FormatException` est
        // la classe de base de `UriFormatException` ET du cas où `url` ne
        // parse même pas comme URI relative/absolue.
        HttpRequestMessage request;
        try
        {
            request = new HttpRequestMessage(HttpMethod.Get, url);
        }
        catch (FormatException ex)
        {
            return new HttpProbeResult(HttpProbeOutcome.UnknownFailure, null, $"URL invalide : {ex.Message}");
        }

        using var _ = request;
        if (bearerToken is not null)
        {
            request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", bearerToken);
        }

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        cts.CancelAfter(timeout);

        try
        {
            using var response = await httpClient.SendAsync(request, cts.Token).ConfigureAwait(false);
            if (response.IsSuccessStatusCode)
            {
                return new HttpProbeResult(HttpProbeOutcome.Ok, (int)response.StatusCode, "HTTP " + (int)response.StatusCode);
            }
            return new HttpProbeResult(HttpProbeOutcome.HttpError, (int)response.StatusCode, $"HTTP {(int)response.StatusCode}");
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return new HttpProbeResult(HttpProbeOutcome.Timeout, null, $"aucune réponse sous {timeout.TotalSeconds:0}s");
        }
        catch (HttpRequestException ex)
        {
            var refused = ex.InnerException is SocketException { SocketErrorCode: SocketError.ConnectionRefused }
                || ex.HttpRequestError == HttpRequestError.ConnectionError;
            return refused
                ? new HttpProbeResult(HttpProbeOutcome.ConnectionRefused, null, "connexion refusée — le service n'écoute pas sur ce port")
                : new HttpProbeResult(HttpProbeOutcome.UnknownFailure, null, ex.Message);
        }
    }
}
