namespace ApexSetup.Core.Contracts;

/// <summary>
/// Sévérité d'un check Doctor — valeurs EXACTES (minuscules) de
/// <c>apex.doctor.check.Severity</c>. Jamais une 5ᵉ valeur sans bump majeur
/// de <c>DOCTOR_SCHEMA_VERSION</c> côté Runtime (voir docs/JSON_CONTRACTS.md).
/// </summary>
public enum DoctorSeverity
{
    Pass,
    Warn,
    Fail,
    Skip,
}

/// <summary>
/// Un résultat de check individuel — miroir de <c>CheckResult.to_dict()</c>.
/// <c>Facts</c> est délibérément à forme libre par check (documenté côté
/// Runtime comme "jamais garanti stable entre checks") : Setup ne doit
/// jamais itérer dessus pour une décision, seulement lire une clé qu'il
/// connaît déjà.
/// </summary>
public sealed record DoctorCheckResult
{
    public required string Name { get; init; }
    public required string Category { get; init; }
    public required DoctorSeverity Severity { get; init; }
    public required string Message { get; init; }
    public required string Remediation { get; init; }
    public required IReadOnlyDictionary<string, object?> Facts { get; init; }
    public required double DurationMs { get; init; }
}

/// <summary>
/// Miroir de <c>DoctorReport.to_dict()</c> (<c>apex doctor --json</c>).
///
/// IMPORTANT — compatibilité descendante volontaire : <c>SchemaVersion</c>,
/// <c>ApexVersion</c> et <c>GeneratedAt</c> sont NULLABLES. Un Runtime
/// construit depuis un commit antérieur à l'enrichissement du contrat
/// Doctor (Phase 1 du dépôt DEV) ne les émet pas — ce n'est PAS un contrat
/// invalide, c'est le socle stable (<c>status</c>/<c>counts</c>/
/// <c>duration_ms</c>/<c>results</c>) qui a toujours existé. Voir
/// <see cref="RuntimeSchemaPolicy"/> pour la règle de refus sur une version
/// supérieure à celle connue.
/// </summary>
public sealed record RuntimeDoctorResult
{
    public int? SchemaVersion { get; init; }
    public string? ApexVersion { get; init; }
    public string? GeneratedAt { get; init; }
    public required DoctorSeverity Status { get; init; }
    public required IReadOnlyDictionary<string, int> Counts { get; init; }
    public required double DurationMs { get; init; }
    public required IReadOnlyList<DoctorCheckResult> Results { get; init; }
}
