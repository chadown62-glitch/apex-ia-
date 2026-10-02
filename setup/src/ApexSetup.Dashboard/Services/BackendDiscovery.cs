using System.Globalization;
using System.Text.Json;

namespace ApexSetup.Dashboard.Services;

/// <summary>L'enregistrement écrit par <c>apex serve</c> (<c>apex/api/discovery.py</c>) — aucun secret.</summary>
public sealed record DiscoveryRecord(
    int Schema,
    string ApexVersion,
    int PanelProtocol,
    string ProjectRoot,
    string TokenFile,
    string Host,
    int Port,
    int Pid,
    DateTimeOffset StartedAt,
    string Phase = "serving",
    StartRefusal? Refusal = null)
{
    /// <summary>`launch.sh serve` prépare le démarrage (compose, doctor, gate) : l'API n'écoute pas encore, c'est normal.</summary>
    public bool IsStarting => Phase == "starting";

    /// <summary>Le dernier démarrage n'a pas eu lieu (phase <c>refused</c>) : <see cref="Refusal"/> dit pourquoi.</summary>
    public bool IsRefused => Phase == "refused";
}

/// <summary>
/// Pourquoi le dernier démarrage n'a pas eu lieu (<c>apex/discovery.py</c>, <c>write_refused</c>) :
/// code de sortie de <c>launch.sh</c>, étape, contrôles du Gate cités par le rapport de CE démarrage.
/// </summary>
public sealed record StartRefusal(int ExitCode, string Stage, IReadOnlyList<string> Blocking, IReadOnlyList<string> UnverifiedBlocking)
{
    /// <summary>Une VÉRIFICATION a refusé (Startup Gate, apex doctor) — à distinguer d'une panne (Docker, erreur interne).</summary>
    public bool IsVerdict => Stage is "gate" or "doctor";
}

public enum DiscoveryKind
{
    /// <summary>Non lu (hôte arrêté : on ne lit rien pour ne rien démarrer).</summary>
    NotRead,
    Found,
    Missing,
    Invalid,
}

public sealed record DiscoveryRead(DiscoveryKind Kind, DiscoveryRecord? Record, string Detail)
{
    public static DiscoveryRead NotRead { get; } = new(DiscoveryKind.NotRead, null, "non lu");
}

/// <summary>Analyse STRICTE : un champ absent, d'un mauvais type ou hors bornes rend l'enregistrement invalide, jamais « à peu près bon ».</summary>
public static class BackendDiscovery
{
    public const string RelativePath = ".apex/runtime/backend.json";

    public static DiscoveryRead FromFile(FileRead read) => read.Kind switch
    {
        FileReadKind.NotFound => new DiscoveryRead(DiscoveryKind.Missing, null, "aucun backend enregistré (launch.sh n'a pas démarré apex serve)"),
        FileReadKind.Failed => new DiscoveryRead(DiscoveryKind.Invalid, null, $"enregistrement de découverte illisible : {read.Detail}"),
        _ => Parse(read.Content ?? string.Empty),
    };

    public static DiscoveryRead Parse(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object || Str(root, "service") != "apex")
            {
                return Invalid("ce fichier n'est pas un enregistrement APEX");
            }
            var record = new DiscoveryRecord(
                Schema: Int(root, "schema"),
                ApexVersion: Str(root, "apex_version") ?? throw new FormatException("apex_version"),
                PanelProtocol: Int(root, "panel_protocol"),
                ProjectRoot: Str(root, "project_root") ?? throw new FormatException("project_root"),
                TokenFile: Str(root, "token_file") ?? throw new FormatException("token_file"),
                Host: Str(root, "host") ?? throw new FormatException("host"),
                Port: Int(root, "port"),
                Pid: Int(root, "pid"),
                StartedAt: DateTimeOffset.Parse(Str(root, "started_at") ?? throw new FormatException("started_at"), CultureInfo.InvariantCulture),
                Phase: Str(root, "phase") ?? "serving");
            if (record.Phase is not ("starting" or "serving" or "refused"))
            {
                return Invalid($"phase inconnue « {record.Phase} »");
            }
            if (record.IsRefused)
            {
                record = record with { Refusal = ParseRefusal(root) };
            }
            if (record.Port is < 1 or > 65535 || record.Pid < 1)
            {
                return Invalid("port ou pid hors bornes");
            }
            if (!HostPaths.IsSafeAbsolute(record.ProjectRoot) || !HostPaths.IsSafeRelative(record.TokenFile))
            {
                return Invalid("chemins de l'enregistrement refusés");
            }
            if (!IsLocalListenAddress(record.Host))
            {
                // Le jeton accompagne chaque requête : jamais vers une autre machine, quoi que dise le fichier.
                return Invalid($"hôte non local refusé (« {record.Host} ») : le Panel n'interroge que cette machine");
            }
            return new DiscoveryRead(DiscoveryKind.Found, record, $"backend enregistré (pid {record.Pid}, port {record.Port})");
        }
        catch (Exception ex) when (ex is JsonException or FormatException or InvalidOperationException or KeyNotFoundException)
        {
            return Invalid($"format invalide ({ex.Message})");
        }
    }

    /// <summary>Stricte, comme le reste : code borné, étape connue, identifiants de contrôles courts et sans caractère spécial.</summary>
    private static StartRefusal ParseRefusal(JsonElement root)
    {
        if (!root.TryGetProperty("refusal", out var refusal) || refusal.ValueKind != JsonValueKind.Object)
        {
            throw new FormatException("refusal");
        }
        var code = Int(refusal, "exit_code");
        var stage = Str(refusal, "stage") ?? throw new FormatException("stage");
        if (code is < 1 or > 255 || stage is not ("infrastructure" or "doctor" or "gate" or "gate_error" or "unknown"))
        {
            throw new FormatException("refusal");
        }
        return new StartRefusal(code, stage, CheckIds(refusal, "blocking"), CheckIds(refusal, "unverified_blocking"));
    }

    private static List<string> CheckIds(JsonElement refusal, string name)
    {
        if (!refusal.TryGetProperty(name, out var list) || list.ValueKind != JsonValueKind.Array || list.GetArrayLength() > 50)
        {
            throw new FormatException(name);
        }
        var ids = new List<string>();
        foreach (var item in list.EnumerateArray())
        {
            var id = item.ValueKind == JsonValueKind.String ? item.GetString() : null;
            if (id is null || id.Length is 0 or > 40 || !id.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_'))
            {
                throw new FormatException(name);
            }
            ids.Add(id);
        }
        return ids;
    }

    public static string TokenPath(DiscoveryRecord record) => record.ProjectRoot.TrimEnd('/') + "/" + record.TokenFile;

    /// <summary>Adresse à interroger depuis le Panel : une écoute sur toutes les interfaces ou sur la boucle locale se joint par 127.0.0.1 (redirection localhost de WSL2).</summary>
    public static string ReachableHost(string? host) =>
        host is null or "" or "0.0.0.0" or "127.0.0.1" or "localhost" or "::" or "::1" ? "127.0.0.1" : host;

    /// <summary>Adresses d'écoute locales : toutes interfaces, boucle locale IPv4 (127.0.0.0/8) ou IPv6.</summary>
    public static bool IsLocalListenAddress(string host) =>
        host is "0.0.0.0" or "::" or "::1" or "localhost"
        || (System.Net.IPAddress.TryParse(host, out var address) && System.Net.IPAddress.IsLoopback(address));

    private static DiscoveryRead Invalid(string detail) => new(DiscoveryKind.Invalid, null, $"enregistrement de découverte invalide : {detail}");

    private static string? Str(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static int Int(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)
            ? number
            : throw new FormatException(name);
}

public enum TokenKind
{
    NotRead,
    Ok,
    Missing,
    Invalid,
}

/// <summary>Le jeton n'est JAMAIS exposé par <see cref="ToString"/> ni par <see cref="Detail"/>.</summary>
public sealed class TokenRead
{
    private TokenRead(TokenKind kind, string? value, string detail)
    {
        Kind = kind;
        Value = value;
        Detail = detail;
    }

    public TokenKind Kind { get; }

    /// <summary>Le secret lui-même : en mémoire seulement, jamais journalisé ni affiché.</summary>
    public string? Value { get; }

    public string Detail { get; }

    public static TokenRead NotRead { get; } = new(TokenKind.NotRead, null, "non lu");

    public static TokenRead FromFile(FileRead read)
    {
        if (read.Kind == FileReadKind.NotFound)
        {
            return new TokenRead(TokenKind.Missing, null, "jeton API absent (.apex/api-token)");
        }
        if (read.Kind == FileReadKind.Failed)
        {
            return new TokenRead(TokenKind.Invalid, null, $"jeton API illisible : {read.Detail}");
        }
        var token = (read.Content ?? string.Empty).Trim();
        var wellFormed = token.Length is >= 16 and <= 512 && token.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_');
        return wellFormed
            ? new TokenRead(TokenKind.Ok, token, "jeton API lu (en mémoire uniquement)")
            : new TokenRead(TokenKind.Invalid, null, "jeton API au format inattendu");
    }

    public override string ToString() => $"TokenRead({Kind})";
}
