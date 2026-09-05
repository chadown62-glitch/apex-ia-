using ApexSetup.Core.RuntimeAdapter;

namespace ApexSetup.Core.Json;

/// <summary>
/// Levée par les parseurs de contrat pour un JSON structurellement invalide
/// (champ obligatoire absent, type inattendu, valeur d'enum inconnue,
/// schema_version incompatible) — jamais utilisée pour un JSON simplement
/// syntaxiquement invalide (voir <see cref="RuntimeCallOutcome.InvalidJson"/>,
/// géré séparément avant même d'atteindre un parseur de contrat).
/// </summary>
public sealed class ContractValidationException(RuntimeCallOutcome outcome, string message) : Exception(message)
{
    public RuntimeCallOutcome Outcome { get; } = outcome;
}
