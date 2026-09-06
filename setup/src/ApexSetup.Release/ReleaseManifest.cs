namespace ApexSetup.Release;

/// <summary>
/// Métadonnées de release pour UN artefact (ex. `apex-setup.exe`) —
/// Phase 7. Chaque champ manquant/inconnu est représenté honnêtement par
/// `null`, jamais une valeur inventée (voir <see cref="RuntimeVersion"/> :
/// Setup n'embarque aucun binaire Runtime, donc toujours null aujourd'hui
/// — pas une omission, un fait).
/// </summary>
public sealed record ReleaseManifest
{
    public const int CurrentSchemaVersion = 1;

    public required int SchemaVersion { get; init; }
    public required string ProductName { get; init; }
    public required string SetupVersion { get; init; }

    /// <summary>Version du Runtime Python contre laquelle ce Setup a été construit/testé — <c>null</c> tant qu'aucun artefact Runtime n'est co-packagé (voir docs/PACKAGING.md).</summary>
    public string? RuntimeVersion { get; init; }

    /// <summary>SHA Git du commit source à partir duquel l'artefact a été construit — traçabilité Source→build (Phase 8). <c>null</c> si indéterminable (ex. répertoire de travail hors dépôt Git).</summary>
    public string? BuildCommitSha { get; init; }

    public required string ArtifactFileName { get; init; }
    public required long ArtifactSizeBytes { get; init; }
    public required string ArtifactSha256 { get; init; }
    public required string Architecture { get; init; }

    /// <summary>Horodatage de construction — EXPLICITEMENT le seul champ non déterministe du manifeste (voir <see cref="ReleaseManifestBuilder"/>).</summary>
    public required string CreatedAtUtc { get; init; }
}
