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
    string Phase = "serving")
{
    /// <summary>`launch.sh serve` prépare le démarrage (compose, doctor, gate) : l'API n'écoute pas encore, c'est normal.</summary>
    public bool IsStarting => Phase == "starting";
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
            if (record.Phase is not ("starting" or "serving"))
            {
                return Invalid($"phase inconnue « {record.Phase} »");
            }
            if (record.Port is < 1 or > 65535 || record.Pid < 1)
            {
                return Invalid("port ou pid hors bornes");
            }
            if (!HostPaths.IsSafeAbsolute(record.ProjectRoot) || !HostPaths.IsSafeRelative(record.TokenFile))
            {
                return Invalid("chemins de l'enregistrement refusés");
            }
            return new DiscoveryRead(DiscoveryKind.Found, record, $"backend enregistré (pid {record.Pid}, port {record.Port})");
        }
        catch (Exception ex) when (ex is JsonException or FormatException or InvalidOperationException or KeyNotFoundException)
        {
            return Invalid($"format invalide ({ex.Message})");
        }
    }

    public static string TokenPath(DiscoveryRecord record) => record.ProjectRoot.TrimEnd('/') + "/" + record.TokenFile;

    /// <summary>Adresse à interroger depuis le Panel : une écoute sur toutes les interfaces ou sur la boucle locale se joint par 127.0.0.1 (redirection localhost de WSL2).</summary>
    public static string ReachableHost(string? host) =>
        host is null or "" or "0.0.0.0" or "127.0.0.1" or "localhost" or "::" ? "127.0.0.1" : host;

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
