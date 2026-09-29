namespace ApexSetup.Dashboard.Models;

/// <summary>Les lignes du tableau d'état du Panel, dans l'ordre d'affichage.</summary>
public enum ComponentKey
{
    Wsl,
    Backend,
    Api,
    Database,
    Redis,
    Ollama,
    Sandbox,
    Brain,
}

public static class ComponentKeys
{
    /// <summary>Services mesurés PAR le backend (<c>GET /api/v1/status</c>), avec leur nom côté backend.</summary>
    public static readonly IReadOnlyList<(ComponentKey Key, string BackendName)> BackendServices =
    [
        (ComponentKey.Database, "database"),
        (ComponentKey.Redis, "redis"),
        (ComponentKey.Ollama, "ollama"),
        (ComponentKey.Sandbox, "sandbox"),
        (ComponentKey.Brain, "brain"),
    ];

    public static string Label(ComponentKey key) => key switch
    {
        ComponentKey.Wsl => "WSL",
        ComponentKey.Backend => "Backend",
        ComponentKey.Api => "API",
        ComponentKey.Database => "Database",
        ComponentKey.Redis => "Redis",
        ComponentKey.Ollama => "Ollama",
        ComponentKey.Sandbox => "Sandbox",
        ComponentKey.Brain => "Brain",
        _ => key.ToString(),
    };
}

/// <summary>Un état affiché — toujours produit par une mesure réelle, jamais fabriqué.</summary>
public sealed record ComponentState(ComponentKey Key, ComponentStatus Status, string Detail)
{
    public string Label => ComponentKeys.Label(Key);
}
