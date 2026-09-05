namespace ApexSetup.Core.RuntimeAdapter;

/// <summary>
/// Classification EXHAUSTIVE et EXPLICITE de l'issue d'un appel Runtime —
/// jamais un simple booléen succès/échec. Chaque valeur correspond à un
/// cas distinct exigé par la spécification (processus introuvable, timeout,
/// JSON invalide, contrat incompatible, etc.).
/// </summary>
public enum RuntimeCallOutcome
{
    Success,
    ProcessNotFound,
    Timeout,
    NonZeroExitCode,
    EmptyStdout,
    InvalidJson,
    IncompatibleSchemaVersion,
    MissingRequiredField,
    UnexpectedFieldType,
    InvalidEnumValue,
}

/// <summary>
/// Résultat typé d'un appel Runtime — porte toujours assez d'information
/// pour diagnostiquer un échec sans deviner (stdout/stderr/exit code
/// bruts conservés, jamais perdus).
/// </summary>
public sealed record RuntimeCallResult<T>
{
    public required RuntimeCallOutcome Outcome { get; init; }
    public T? Value { get; init; }
    public string? ErrorDetail { get; init; }
    public string RawStdout { get; init; } = string.Empty;
    public string RawStderr { get; init; } = string.Empty;
    public int? ExitCode { get; init; }

    public bool IsSuccess => Outcome == RuntimeCallOutcome.Success;

    public static RuntimeCallResult<T> Success(T value, string stdout, int exitCode) => new()
    {
        Outcome = RuntimeCallOutcome.Success,
        Value = value,
        RawStdout = stdout,
        ExitCode = exitCode,
    };

    public static RuntimeCallResult<T> Failure(
        RuntimeCallOutcome outcome, string errorDetail, string stdout = "", string stderr = "", int? exitCode = null) => new()
    {
        Outcome = outcome,
        ErrorDetail = errorDetail,
        RawStdout = stdout,
        RawStderr = stderr,
        ExitCode = exitCode,
    };
}
