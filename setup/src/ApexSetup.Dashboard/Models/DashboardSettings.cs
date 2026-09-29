namespace ApexSetup.Dashboard.Models;

/// <summary>
/// Paramètres utilisateur du Panel — facultatifs : sans eux, tout est découvert automatiquement
/// (distribution WSL par défaut, port et racine du projet lus dans l'enregistrement de découverte du
/// backend). Aucun secret ici : le jeton API n'est jamais écrit côté Windows. Les anciens champs
/// (chemin de projet saisi, port du serveur web, etc.) d'un fichier existant sont ignorés à la
/// lecture.
/// </summary>
public sealed record DashboardSettings
{
    /// <summary><c>null</c>/vide = distribution WSL par défaut du système (comportement d'<c>apex.bat</c>).</summary>
    public string? WslDistro { get; init; }

    /// <summary>Port de l'API si aucun backend n'est enregistré (sinon le port découvert prime). <c>null</c> = 8000, le défaut d'<c>apex serve</c>.</summary>
    public int? ApiPort { get; init; }

    public const int DefaultApiPort = 8000;
}
