using System.Text.Json;
using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Core.Json;

/// <summary>
/// Accès stricts à un <see cref="JsonElement"/> — chaque méthode lève
/// <see cref="ContractValidationException"/> avec l'outcome précis
/// (<c>MissingRequiredField</c>/<c>UnexpectedFieldType</c>) plutôt que de
/// renvoyer une valeur par défaut silencieuse. Aucune méthode ici
/// n'interprète un texte humain — uniquement des clés/types JSON exacts.
/// </summary>
internal static class JsonHelpers
{
    public static JsonElement RequireProperty(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var value))
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.MissingRequiredField, $"champ obligatoire absent : « {name} »");
        }
        return value;
    }

    public static string RequireString(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.String)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu chaîne, reçu {value.ValueKind}");
        }
        return value.GetString()!;
    }

    public static string? OptionalString(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
        {
            return null;
        }
        if (value.ValueKind != JsonValueKind.String)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu chaîne ou null, reçu {value.ValueKind}");
        }
        return value.GetString();
    }

    public static int RequireInt(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out var result))
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu entier, reçu {value.ValueKind}");
        }
        return result;
    }

    public static int? OptionalInt(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
        {
            return null;
        }
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out var result))
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu entier ou null, reçu {value.ValueKind}");
        }
        return result;
    }

    public static double RequireDouble(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.Number)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu nombre, reçu {value.ValueKind}");
        }
        return value.GetDouble();
    }

    public static double? OptionalDouble(JsonElement obj, string name)
    {
        if (!obj.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
        {
            return null;
        }
        if (value.ValueKind != JsonValueKind.Number)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu nombre ou null, reçu {value.ValueKind}");
        }
        return value.GetDouble();
    }

    public static bool RequireBool(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.True && value.ValueKind != JsonValueKind.False)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu booléen, reçu {value.ValueKind}");
        }
        return value.GetBoolean();
    }

    public static JsonElement RequireObject(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.Object)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu objet, reçu {value.ValueKind}");
        }
        return value;
    }

    public static JsonElement RequireArray(JsonElement obj, string name)
    {
        var value = RequireProperty(obj, name);
        if (value.ValueKind != JsonValueKind.Array)
        {
            throw new ContractValidationException(
                RuntimeCallOutcome.UnexpectedFieldType, $"« {name} » attendu tableau, reçu {value.ValueKind}");
        }
        return value;
    }

    public static IReadOnlyDictionary<string, int> RequireIntDictionary(JsonElement obj, string name)
    {
        var value = RequireObject(obj, name);
        var result = new Dictionary<string, int>();
        foreach (var prop in value.EnumerateObject())
        {
            if (prop.Value.ValueKind != JsonValueKind.Number || !prop.Value.TryGetInt32(out var n))
            {
                throw new ContractValidationException(
                    RuntimeCallOutcome.UnexpectedFieldType, $"« {name}.{prop.Name} » attendu entier, reçu {prop.Value.ValueKind}");
            }
            result[prop.Name] = n;
        }
        return result;
    }

    /// <summary>
    /// Conversion libre JsonElement → objet CLR — utilisée UNIQUEMENT pour
    /// les champs à forme volontairement libre côté Runtime (<c>facts</c>,
    /// <c>capabilities</c>) : jamais lue aveuglément par l'appelant pour
    /// une décision, seulement pour des clés déjà connues.
    /// </summary>
    public static IReadOnlyDictionary<string, object?> ToFreeFormDictionary(JsonElement obj)
    {
        var result = new Dictionary<string, object?>();
        foreach (var prop in obj.EnumerateObject())
        {
            result[prop.Name] = ToFreeFormValue(prop.Value);
        }
        return result;
    }

    private static object? ToFreeFormValue(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString(),
        JsonValueKind.Number => value.TryGetInt64(out var l) ? l : value.GetDouble(),
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Null => null,
        JsonValueKind.Object => ToFreeFormDictionary(value),
        JsonValueKind.Array => value.EnumerateArray().Select(ToFreeFormValue).ToList(),
        _ => null,
    };
}
