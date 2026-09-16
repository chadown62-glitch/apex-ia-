namespace ApexSetup.Dashboard.Services;

/// <summary>
/// Classification d'une tentative HTTP réelle — mission §10 : « HTTP
/// unreachable / Connection refused / Timeout / HTTP 500 » selon la cause
/// réellement détectée, jamais un statut binaire.
/// </summary>
public enum HttpProbeOutcome
{
    Ok,
    ConnectionRefused,
    Timeout,
    HttpError,
    UnknownFailure,
}

public sealed record HttpProbeResult(HttpProbeOutcome Outcome, int? StatusCode, string Detail)
{
    public bool IsOk => Outcome == HttpProbeOutcome.Ok;
}
