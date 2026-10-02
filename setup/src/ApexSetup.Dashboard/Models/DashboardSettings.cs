namespace ApexSetup.Dashboard.Models;

/// <summary>
/// Paramètres utilisateur du Panel — facultatifs : sans eux, tout est découvert automatiquement
/// (distribution WSL par défaut, port et racine du projet lus dans l'enregistrement de découverte du
/// backend). Aucun secret ici : le jeton API n'est jamais écrit côté Windows. Les champs inconnus
/// d'un fichier existant sont ignorés à la lecture.
/// </summary>
public sealed record DashboardSettings
{
    /// <summary><c>null</c>/vide = distribution WSL par défaut du système (comportement d'<c>apex.bat</c>).</summary>
    public string? WslDistro { get; init; }

    /// <summary>Port de l'API si aucun backend n'est enregistré (sinon le port découvert prime). <c>null</c> = 8000, le défaut d'<c>apex serve</c>.</summary>
    public int? ApiPort { get; init; }

    public const int DefaultApiPort = 8000;

    /// <summary>
    /// Dossier APEX dans WSL (ou sur cette machine Linux), saisi par l'utilisateur : chemin absolu
    /// (<c>/home/moi/apex</c>) ou relatif au dossier personnel (<c>apex</c> = <c>~/apex</c>). Seul
    /// le bouton « Démarrer APEX » l'utilise. <c>null</c> = <see cref="LastProjectRoot"/>, sinon
    /// <see cref="DefaultProjectRoot"/>.
    /// </summary>
    public string? ProjectRoot { get; init; }

    /// <summary>
    /// Dossier du dernier backend APEX qui a répondu AVEC le jeton lu dans ce dossier — jamais un
    /// chemin lu dans un enregistrement de découverte non vérifié (un fichier altéré ne peut pas
    /// choisir ce que le Panel exécutera). Mémorisé automatiquement.
    /// </summary>
    public string? LastProjectRoot { get; init; }

    /// <summary><c>~/apex</c> : l'emplacement documenté d'installation du dépôt.</summary>
    public const string DefaultProjectRoot = "apex";

    /// <summary>Le dossier que « Démarrer APEX » utilisera, et d'où il vient.</summary>
    public (string Path, string Source) EffectiveProjectRoot() =>
        !string.IsNullOrWhiteSpace(ProjectRoot) ? (ProjectRoot.Trim(), "Paramètres")
        : !string.IsNullOrWhiteSpace(LastProjectRoot) ? (LastProjectRoot, "dernier dossier où APEX a tourné")
        : (DefaultProjectRoot, "par défaut (~/apex)");
}
