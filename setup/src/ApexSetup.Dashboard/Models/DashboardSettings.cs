namespace ApexSetup.Dashboard.Models;

/// <summary>
/// Paramètres persistés du Dashboard — mission §7/§16 : « prévoir une
/// configuration permettant de modifier la distribution ou le chemin »,
/// « afficher les valeurs réellement utilisées », « ne jamais afficher de
/// secret ». Rien ici n'est un secret (le jeton API vit exclusivement côté
/// WSL, dans <c>.apex/api-token</c> — jamais recopié dans ce fichier, voir
/// <see cref="Services.IApiHealthChecker"/>).
///
/// <see cref="ProjectPathWsl"/> est délibérément <c>null</c> par défaut —
/// la mission interdit explicitement de coder en dur le chemin observé sur
/// une machine particulière (<c>/home/spy/projects/apex-project</c>) sans
/// vérification. <see cref="SuggestedProjectPathWsl"/> porte cette valeur
/// à titre de SUGGESTION affichée dans les Paramètres, jamais utilisée
/// directement tant qu'elle n'a pas été confirmée (bouton « Vérifier »,
/// voir <see cref="Services.IWslProbe.PathExistsAsync"/>) puis enregistrée
/// explicitement par l'utilisateur.
/// </summary>
public sealed record DashboardSettings
{
    /// <summary>Suggestion affichée en Paramètres — jamais utilisée telle quelle par une action réelle. Voir docstring de la classe.</summary>
    public const string SuggestedProjectPathWsl = "/home/spy/projects/apex-project";

    /// <summary><c>null</c>/vide = distribution WSL par défaut du système (comportement d'<c>apex.bat</c> sans <c>APEX_WSL_DISTRO</c>).</summary>
    public string? WslDistro { get; init; }

    /// <summary>Chemin WSL vers la racine du dépôt APEX. <c>null</c> tant que l'utilisateur ne l'a pas configuré et vérifié — jamais un défaut supposé valide.</summary>
    public string? ProjectPathWsl { get; init; }

    public string ApiHost { get; init; } = "127.0.0.1";

    public int ApiPort { get; init; } = 8000;

    /// <summary>Port du serveur de développement Vite (<c>frontend/vite.config.ts</c> ne fixe aucun port explicite — 5173 est le défaut Vite).</summary>
    public int WebPanelPort { get; init; } = 5173;

    public string OllamaUrl { get; init; } = "http://127.0.0.1:11434";

    /// <summary>Jamais activé par défaut — mission §17, doit rester explicitement activé par l'utilisateur.</summary>
    public bool LaunchOnWindowsStartup { get; init; }

    public string ApiBaseUrl => $"http://{ApiHost}:{ApiPort}";

    public string WebPanelUrl => $"http://127.0.0.1:{WebPanelPort}";
}
