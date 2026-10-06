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
    Models,
    Sandbox,
    Brain,
    Ui,
}

public static class ComponentKeys
{
    /// <summary>Services mesurés PAR le backend (<c>GET /api/v1/status</c>), avec leur nom côté backend.</summary>
    public static readonly IReadOnlyList<(ComponentKey Key, string BackendName)> BackendServices =
    [
        (ComponentKey.Database, "database"),
        (ComponentKey.Redis, "redis"),
        (ComponentKey.Ollama, "ollama"),
        (ComponentKey.Models, "models"),
        (ComponentKey.Sandbox, "sandbox"),
        (ComponentKey.Brain, "brain"),
        (ComponentKey.Ui, "ui"),
    ];

    public static string Label(ComponentKey key) => key switch
    {
        ComponentKey.Wsl => "WSL",
        ComponentKey.Backend => "Backend",
        ComponentKey.Api => "API",
        ComponentKey.Database => "Database",
        ComponentKey.Redis => "Redis",
        ComponentKey.Ollama => "Ollama",
        ComponentKey.Models => "Modèles",
        ComponentKey.Sandbox => "Sandbox",
        ComponentKey.Brain => "Brain",
        ComponentKey.Ui => "Interface",
        _ => key.ToString(),
    };

    /// <summary>Lignes affichées mais hors du verdict, comme côté backend (<c>component_checks.INFORMATIONAL</c>) :
    /// Ollama n'est qu'un backend de modèles facultatif ; « Modèles » mesure s'il existe un modèle exécutable.</summary>
    public static bool IsInformational(ComponentKey key) => key == ComponentKey.Ollama;
}

/// <summary>Un état affiché — toujours produit par une mesure réelle, jamais fabriqué.</summary>
public sealed record ComponentState(ComponentKey Key, ComponentStatus Status, string Detail)
{
    public string Label => ComponentKeys.Label(Key);
}
