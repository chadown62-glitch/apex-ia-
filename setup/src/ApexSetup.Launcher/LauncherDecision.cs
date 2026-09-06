namespace ApexSetup.Launcher;

/// <summary>
/// Résultat pur de <see cref="LauncherStateEngine.Derive"/> — jamais
/// d'effet de bord porté par ce type lui-même ; <c>Program</c> décide quoi
/// FAIRE de cette décision (afficher un message, appeler
/// <c>RecoverAsync</c>, démarrer le Runtime...), ce type se contente de la
/// DÉCRIRE.
/// </summary>
public sealed record LauncherDecision
{
    public required LauncherOutcome Outcome { get; init; }

    /// <summary>Texte technique — jamais affiché brut à l'utilisateur (voir §F GUI/UX : un message utilisateur propre est construit séparément à partir de <see cref="Outcome"/>). Utile pour les logs (§G) et le mode <c>--details</c>.</summary>
    public required string Detail { get; init; }

    /// <summary>Vrai uniquement pour <see cref="LauncherOutcome.NeedsRecovery"/> — indique qu'une réparation automatique existe et peut être tentée sans confirmation humaine supplémentaire (elle reste elle-même soumise à <c>RecoverAsync</c>, qui ne répare jamais silencieusement un état incertain).</summary>
    public required bool AutoRecoverable { get; init; }
}
