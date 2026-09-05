using ApexSetup.Core.Contracts;

namespace ApexSetup.Core.Pipeline;

/// <summary>
/// État partagé traversant le pipeline — un seul par exécution. Les
/// étapes y déposent ce dont les étapes suivantes ont besoin (chemin des
/// artefacts téléchargés, etc.) via <see cref="Items"/>, une table libre
/// mais typée à la lecture (jamais un objet dynamique non vérifié).
/// </summary>
public sealed class InstallationContext
{
    public required InstallationPlan Plan { get; init; }

    /// <summary>Répertoire de travail pour les fichiers temporaires/téléchargements — jamais la destination finale.</summary>
    public required string WorkingDirectory { get; init; }

    /// <summary>Répertoire de destination finale de l'installation (miroir de <c>Plan.Destination</c>, résolu en chemin absolu).</summary>
    public required string DestinationDirectory { get; init; }

    private readonly Dictionary<string, object> _items = new();

    public void SetItem<T>(string key, T value) where T : notnull => _items[key] = value;

    public T GetRequiredItem<T>(string key)
    {
        if (!_items.TryGetValue(key, out var value))
        {
            throw new InvalidOperationException($"élément de contexte attendu absent : « {key} » — une étape antérieure ne l'a pas déposé.");
        }
        if (value is not T typed)
        {
            throw new InvalidOperationException($"élément de contexte « {key} » de type inattendu : {value.GetType()} (attendu {typeof(T)}).");
        }
        return typed;
    }

    public bool TryGetItem<T>(string key, out T? value)
    {
        if (_items.TryGetValue(key, out var raw) && raw is T typed)
        {
            value = typed;
            return true;
        }
        value = default;
        return false;
    }
}
