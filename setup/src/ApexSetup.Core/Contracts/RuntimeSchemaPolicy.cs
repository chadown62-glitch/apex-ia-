namespace ApexSetup.Core.Contracts;

/// <summary>
/// Compteurs de version CONNUS par ce Setup, un par contrat — jamais
/// couplés entre eux (même règle que côté Runtime, docs/JSON_CONTRACTS.md :
/// Doctor et Gate incrémentent leur <c>schema_version</c> indépendamment).
/// </summary>
public static class RuntimeSchemaPolicy
{
    /// <summary>Dernier <c>DOCTOR_SCHEMA_VERSION</c> connu de ce Setup.</summary>
    public const int KnownDoctorSchemaVersion = 1;

    /// <summary>Dernier <c>SCHEMA_VERSION</c> Gate connu de ce Setup.</summary>
    public const int KnownGateSchemaVersion = 1;

    /// <summary>
    /// Règle de compatibilité : un <c>schema_version</c> supérieur à celui
    /// connu est une INCONNUE, jamais une supposition de compatibilité —
    /// même discipline que <c>GateStatus.Unknown</c> côté Runtime. Un
    /// schema_version égal ou inférieur est accepté (une version
    /// antérieure du contrat Doctor, avant l'enrichissement Phase 1, est
    /// représentée par <c>null</c> côté <see cref="RuntimeDoctorResult"/>,
    /// jamais par ce chemin).
    /// </summary>
    public static bool IsKnown(int observedSchemaVersion, int knownSchemaVersion) =>
        observedSchemaVersion <= knownSchemaVersion;
}
