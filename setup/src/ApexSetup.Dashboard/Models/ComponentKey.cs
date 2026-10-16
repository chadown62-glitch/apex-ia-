namespace ApexSetup.Dashboard.Models;

/// <summary>Les lignes du tableau d'état du Panel, dans l'ordre d'affichage.</summary>
public enum ComponentKey
{
    Wsl,
    /// <summary>Mode Windows natif : Python et bibliothèques d'APEX préparés sur ce poste (remplace la ligne WSL).</summary>
    Runtime,
    Backend,
    Api,
    Database,
    Redis,
    Ollama,
    Models,
    Sandbox,
    /// <summary>Cerveau APEX (ApexLM) servi : poids intègres, conversationnel.</summary>
    ApexBrain,
    /// <summary>Moteur intégré (transformers, en processus) : poids du modèle par défaut.</summary>
    NativeModel,
    /// <summary>GPU CUDA : accélération facultative mesurée au démarrage, pilote NVIDIA détecté.</summary>
    Gpu,
    GameStudio,
    /// <summary>Planificateur de Game Studio : Qwen2.5-Coder-7B épinglé ou moteur intégré.</summary>
    Planner,
    Blender,
    GameGraphics,
    GameEngineer,
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
        (ComponentKey.ApexBrain, "apex_brain"),
        (ComponentKey.NativeModel, "native_model"),
        (ComponentKey.Gpu, "gpu"),
        (ComponentKey.GameStudio, "game_studio"),
        (ComponentKey.Planner, "planner"),
        (ComponentKey.Blender, "blender"),
        (ComponentKey.GameGraphics, "game_graphics"),
        (ComponentKey.GameEngineer, "game_engineer"),
        (ComponentKey.Brain, "brain"),
        (ComponentKey.Ui, "ui"),
    ];

    public static string Label(ComponentKey key) => key switch
    {
        ComponentKey.Wsl => "WSL",
        ComponentKey.Runtime => "Runtime",
        ComponentKey.Backend => "Backend",
        ComponentKey.Api => "API",
        ComponentKey.Database => "Database",
        ComponentKey.Redis => "Redis",
        ComponentKey.Ollama => "Ollama",
        ComponentKey.Models => "Modèles",
        ComponentKey.Sandbox => "Sandbox",
        ComponentKey.ApexBrain => "Cerveau APEX",
        ComponentKey.NativeModel => "Moteur intégré",
        ComponentKey.Gpu => "GPU CUDA",
        ComponentKey.GameStudio => "Game Studio",
        ComponentKey.Planner => "Planificateur 3D",
        ComponentKey.Blender => "Blender",
        ComponentKey.GameGraphics => "Game Graphics",
        ComponentKey.GameEngineer => "Game Engineer",
        ComponentKey.Brain => "Brain",
        ComponentKey.Ui => "Interface",
        _ => key.ToString(),
    };

    /// <summary>Lignes affichées mais hors du verdict, comme côté backend (<c>component_checks.INFORMATIONAL</c>) :
    /// Ollama n'est qu'un backend de modèles facultatif ; « Modèles » mesure s'il existe un modèle exécutable.</summary>
    public static bool IsInformational(ComponentKey key) => key == ComponentKey.Ollama || IsApplication(key);

    /// <summary>
    /// Composants de l'application (Cerveau APEX, moteur intégré, GPU, Game Studio et ses parties) : mesurés par le
    /// backend (<c>component_checks.APPLICATION</c>), facultatifs, jamais dans le verdict. Un backend plus ancien
    /// qui ne les rapporte pas n'affiche pas leur ligne, plutôt qu'« inconnu ».
    /// </summary>
    public static bool IsApplication(ComponentKey key) => key is ComponentKey.ApexBrain or ComponentKey.NativeModel
        or ComponentKey.Gpu or ComponentKey.GameStudio or ComponentKey.Planner or ComponentKey.Blender
        or ComponentKey.GameGraphics or ComponentKey.GameEngineer;
}

/// <summary>Un état affiché — toujours produit par une mesure réelle, jamais fabriqué.</summary>
public sealed record ComponentState(ComponentKey Key, ComponentStatus Status, string Detail)
{
    public string Label => ComponentKeys.Label(Key);
}
