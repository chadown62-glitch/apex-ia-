namespace ApexSetup.Launcher.Notify;

/// <summary>Une action que l'utilisateur peut choisir depuis un message d'erreur — jamais un simple texte, toujours une action concrète et déterministe.</summary>
public enum UserAction
{
    Repair,
    Details,
    Close,
}

/// <summary>
/// Frontière testable autour de l'affichage utilisateur (§F GUI/UX :
/// « jamais une erreur brute type 'Runtime initialization error 0x...'
/// seule — préférer un message propre avec des boutons style
/// [Réparer]/[Détails]/[Fermer] »). <c>Program</c> ne construit jamais de
/// UI lui-même — il traduit un <see cref="LauncherOutcome"/> en un message
/// humain, puis délègue l'affichage réel à cette interface.
/// </summary>
public interface IUserNotifier
{
    /// <param name="title">Titre court, jamais un code d'erreur brut.</param>
    /// <param name="message">Message compréhensible par un utilisateur non technique — jamais de jargon interne (« GateStatus.Fail », « 0xC0000135 »...).</param>
    /// <param name="technicalDetail">Détail technique complet, affiché SEULEMENT si l'utilisateur choisit [Détails] — jamais montré par défaut, jamais un secret.</param>
    /// <param name="offerRepair">Vrai si un [Réparer] a un sens pour cet état (ex. <see cref="LauncherOutcome.NeedsRecovery"/>) — sinon seuls [Détails]/[Fermer] sont proposés.</param>
    UserAction ShowError(string title, string message, string technicalDetail, bool offerRepair);

    /// <summary>Notification non bloquante, informative uniquement (ex. « élévation en cours... ») — jamais utilisée pour une décision qui requiert un choix utilisateur.</summary>
    void ShowInfo(string title, string message);
}
