namespace ApexSetup.Application.Contracts;

/// <summary>
/// Les 7 états de cycle de vie exigés par la spécification Phase 3 (§10
/// Status) — projection, au niveau CLI, de <c>Pipeline.RecoveryState</c>
/// (4 valeurs côté Core) enrichie de la distinction « en cours » (verrou
/// actuellement tenu) vs « interrompue » (verrou libre, personne
/// n'exécute plus rien) : <c>RecoveryState.InterruptedNeedsRollback</c>
/// seul ne dit pas si un processus travaille encore dessus.
///
/// <see cref="RecoveryNeeded"/> répond À LA FOIS à « installation
/// interrompue » et à « recovery nécessaire » de la spécification : sur le
/// disque, c'est EXACTEMENT la même situation observable (un état persisté
/// « InProgress » sans marqueur de commit, et personne ne détient plus le
/// verrou) — lui donner deux noms différents aurait été une distinction
/// sans différence, jamais mesurable séparément. <see cref="RollbackPerformed"/>
/// n'est jamais retourné par <c>status</c> (une lecture pure, sans action)
/// — uniquement par <c>recover</c>, qui seul peut agir.
/// </summary>
public enum SetupLifecycleState
{
    NoInstallation,
    InProgress,
    Completed,
    RecoveryNeeded,
    RollbackPerformed,
    Inconsistent,
}
