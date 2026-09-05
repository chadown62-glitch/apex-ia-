namespace ApexSetup.Application.Contracts;

/// <summary>
/// Version du contrat JSON exposé par le CLI Setup — INDÉPENDANTE des
/// versions de schéma Runtime (<c>RuntimeSchemaPolicy</c>) : ce contrat
/// décrit la sortie DU CLI SETUP LUI-MÊME, jamais celle du Runtime qu'il
/// interroge. Un futur GUI qui lirait un <c>schema_version</c> supérieur à
/// celui qu'il connaît doit refuser d'interpréter la réponse plutôt que
/// deviner — même politique que côté Runtime (voir docs/JSON_CONTRACTS.md).
/// </summary>
public static class CliContractVersion
{
    public const int Current = 1;
}

/// <summary>
/// Une erreur structurée — jamais un message texte brut seul. <c>Code</c>
/// est un identifiant STABLE (jamais un texte traduit ou reformulé d'une
/// version à l'autre) qu'un GUI peut utiliser pour une décision ; <c>Message</c>
/// est le texte humain ; <c>Detail</c> porte une information technique
/// supplémentaire quand disponible (jamais un secret — voir §15 Logging).
/// </summary>
public sealed record CliError
{
    public required string Code { get; init; }
    public required string Message { get; init; }
    public string? Detail { get; init; }
}

/// <summary>Un avertissement — jamais bloquant par nature (sinon c'est un <see cref="CliError"/>).</summary>
public sealed record CliWarning
{
    public required string Code { get; init; }
    public required string Message { get; init; }
}

/// <summary>
/// Champs COMMUNS à toute réponse du CLI Setup — présents identiquement
/// dans les 8 commandes, pour qu'un GUI puisse toujours lire
/// succès/opération/erreurs/warnings de la même façon avant même de savoir
/// quelle commande a été invoquée. <c>State</c>/<c>CurrentStep</c>/<c>Progress</c>
/// sont nullable : leur pertinence dépend de la commande (ex. « version »
/// n'a ni état ni étape).
/// </summary>
public abstract record CliResponseEnvelope
{
    public int SchemaVersion { get; init; } = CliContractVersion.Current;
    public required string Operation { get; init; }
    public required bool Success { get; init; }
    public string? State { get; init; }
    public string? CurrentStep { get; init; }
    public CliProgress? Progress { get; init; }
    public required IReadOnlyList<CliError> Errors { get; init; }
    public required IReadOnlyList<CliWarning> Warnings { get; init; }
}

/// <summary>Progression d'une opération à étapes (installation) — absente pour les commandes sans notion d'étapes.</summary>
public sealed record CliProgress
{
    public required IReadOnlyList<string> CompletedSteps { get; init; }
    public required int TotalSteps { get; init; }
}
