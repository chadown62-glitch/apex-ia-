namespace ApexSetup.Dashboard.Models;

/// <summary>Les neuf lignes exactes du tableau « System Status » (mission §4).</summary>
public enum ComponentKey
{
    Wsl,
    Docker,
    Postgres,
    Redis,
    MinIO,
    Ollama,
    Gpu,
    Api,
    WebPanel,
}

/// <summary>Un état de composant affiché — toujours produit par une mesure réelle, jamais fabriqué (voir <see cref="ComponentStatus"/>).</summary>
public sealed record ComponentState(ComponentKey Key, string Label, ComponentStatus Status, string Detail)
{
    public static ComponentState Unknown(ComponentKey key, string label, string detail) =>
        new(key, label, ComponentStatus.Unknown, detail);
}
