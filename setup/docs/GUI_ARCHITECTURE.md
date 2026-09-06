# Setup GUI — Architecture (Phase 4)

## Décision de framework

**Avalonia UI 11** (.NET, MVVM, rendu Skia natif — jamais un navigateur
embarqué). Choisi après inspection réelle de cet environnement de
développement :

- `dotnet workload list` : aucun workload Windows Desktop installé, et cet
  environnement est Linux — **WPF et WinUI3 ne peuvent ni être compilés ni
  exécutés ici** (leurs assemblies de référence supposent une cible
  Windows). Les choisir aurait rendu tout le travail des Phases 4-5
  invérifiable dans cet environnement — violerait directement la règle
  « aucune déclaration sans validation réelle ».
- Avalonia compile ET s'exécute réellement sur Linux (vérifié : lancement
  sous Xvfb, fenêtre réelle affichée, aucun crash) tout en publiant un
  exécutable Windows natif pour le RID `win-x64` — le MÊME code source
  produit le binaire de développement (validé ici) et le binaire de
  production (Windows).
- NuGet accessible (vérifié empiriquement avant tout choix — voir
  historique de session) : `Avalonia`, `Avalonia.Desktop`,
  `Avalonia.Themes.Fluent` se restaurent sans problème.
- Pas une pile web lourde : rendu natif Skia, jamais Electron/CEF/un
  navigateur — conforme à la préférence explicite ".NET moderne /
  Windows-native".
- MVVM à la main (`ObservableObject`/`RelayCommand`/`AsyncRelayCommand`,
  ~120 lignes au total dans `Mvvm/`) plutôt que d'ajouter
  CommunityToolkit.Mvvm ou ReactiveUI — évite une dépendance externe
  supplémentaire pour un besoin trivial (règle explicite : « ne pas
  introduire de dépendance externe inutile »).

## Couches

```
ApexSetup.Gui (Views AXAML + ViewModels)
    │  connaît UNIQUEMENT ISetupApplicationService + les types de données
    │  publics de ApexSetup.Application.Contracts/Requests
    ▼
ApexSetup.Application (SetupApplicationService, SetupApplicationServiceFactory)
    │
    ▼
ApexSetup.Core (stages, pipeline, PathSafety, InstallationLock, RecoveryClassifier...)
    │
    ▼
RuntimeAdapter → Runtime CLI Python
```

`ApexSetup.Gui.csproj` ne référence QUE `ApexSetup.Application.csproj` —
jamais `ApexSetup.Core.csproj` (vérifié par
`ArchitectureTests.Gui_project_never_directly_references_the_ApexSetup_Core_assembly_project`).
Un second test, `Gui_assembly_never_references_a_forbidden_Core_internal_type_in_any_signature`,
inspecte par réflexion CHAQUE type du binaire GUI compilé et échoue si
l'un d'eux référence — en champ, propriété, paramètre ou type de retour —
`InstallationStage`, `DownloadStage`, `PathSafety`, `InstallationLock`,
`RecoveryClassifier`, `RuntimeProcessRunner`, `RuntimeAdapter`, ou tout
autre stage/composant interne de pipeline. C'est un test de RÉGRESSION,
pas seulement une convention — toute violation future casse la suite.

`ApexSetup.Application.SetupApplicationServiceFactory` (nouvelle en Phase
4) est la racine de composition PARTAGÉE entre `ApexSetup.Cli` et
`ApexSetup.Gui` — avant cette classe, la construction du service
(`RuntimeAdapter`, `RealEnvironmentProbe`, `RealHardwareDetector`,
`HttpClient`) n'existait que dans `Cli.Program.BuildService` ; un GUI
naïf l'aurait soit dupliquée, soit aurait dû connaître des types Core
directement. Le CLI a été refactoré pour l'utiliser aussi (`Program.cs`,
`BuildService`) — moins de code dupliqué, comportement identique
(vérifié : les 25 tests CLI existants passent inchangés après le
refactor).

## Les 9 écrans (Phase 5)

`Welcome → Detection → Check → Profile → Plan → InstallProgress →
Recovery → Validation → FinalReady` — `MainWindowViewModel` construit les
9 une seule fois (état utilisateur jamais réinitialisé en navigant) et
implémente elle-même `INavigationService`.

Chaque écran appelle EXACTEMENT la méthode `ISetupApplicationService`
correspondant à la commande CLI homonyme (`DetectAsync`/`CheckAsync`/
`PlanAsync`/`InstallAsync`/`RecoverAsync`/`ValidateAsync`/`StatusAsync`)
— jamais une deuxième logique d'installation. Le GUI et le CLI consomment
donc littéralement le même code métier, uniquement la présentation
diffère.

## Pas de progression fabriquée

`ISetupApplicationService.InstallAsync` ne renvoie un résultat qu'une
fois le pipeline terminé — il n'existe aujourd'hui aucune API de
progression PAR ÉTAPE. `InstallProgressViewModel` affiche donc un
indicateur d'activité INDÉTERMINÉ pendant l'appel (`ProgressBar
IsIndeterminate="True"`), jamais un pourcentage inventé. `Committed`
reste `false` tant que `InstallAsync` n'est pas revenu avec
`Committed=true` — impossible d'afficher un succès avant Commit, la même
garantie que le CLI, assurée par `InstallationPipeline` (Core, inchangé).

## Annulation réelle

`AsyncRelayCommand` porte un `CancellationTokenSource` réel, propagé
jusqu'au `CancellationToken` déjà accepté par
`ISetupApplicationService.InstallAsync` — lui-même propagé par
`InstallationPipeline.RunAsync`, qui vérifie le token entre chaque étape
et laisse `OperationCanceledException` remonter sans jamais la travestir
en succès. Testé avec un vrai serveur HTTP local qui ne répond jamais
(`InstallProgressViewModelTests.A_real_cancellation_actually_stops_the_pipeline_and_never_reports_success`).

## Frontière avec Docker/WSL/Ollama

`FinalReadyViewModel` (écran 9) n'invoque JAMAIS Docker/Ollama/le Runtime
lui-même — il affiche l'état final (`setup status`) et un texte renvoyant
l'utilisateur vers le lanceur habituel (`apex.bat`/`apex-launch.sh`).
Cette limite est héritée de l'architecture Setup déjà verrouillée
(« ne lance jamais Docker ou Ollama directement »).

## Revue adversariale (Phase 10) — bugs GUI réels trouvés et corrigés

Une revue adversariale ciblée sur `ApexSetup.Gui` (concurrence,
annulation, gestion d'erreur, fermeture pendant une opération) a trouvé et
corrigé quatre défauts réels — chacun reproduit avant correction, chacun
couvert par un test de régression réel dans `ApexSetup.Gui.Tests` :

1. **Bouton Annuler jamais actionnable** — `CancelCommand.CanExecute()`
   dépendait de `InstallCommand.IsRunning`, mais rien ne notifiait jamais
   `CancelCommand` d'un changement : évalué une seule fois au binding XAML
   (`IsRunning=false` à ce moment), il restait désactivé pendant toute
   installation réelle. Corrigé par un abonnement
   (`InstallCommand.CanExecuteChanged += (_, _) => CancelCommand.RaiseCanExecuteChanged();`).
   Test : `CancelCommand_becomes_executable_once_the_install_actually_starts`.
2. **Une exception inattendue pendant l'installation terminait tout le
   processus GUI** — seule `OperationCanceledException` était capturée
   dans `RunInstallAsync` ; `AsyncRelayCommand.Execute` étant `async void`,
   toute autre exception (levée avant même que le pipeline ne démarre)
   remontait jusqu'à faire planter l'application, sans confirmation de
   rollback ni erreur affichée. Corrigé par un `catch (Exception)`
   supplémentaire, affichant l'erreur au lieu de la laisser fatale. Test :
   `An_unexpected_exception_is_surfaced_as_an_error_never_escapes_uncaught`.
3. **Course entre le déclenchement automatique (arrivée sur l'écran) et le
   bouton manuel de rafraîchissement** — `CheckViewModel`/
   `ValidationViewModel`/`FinalReadyViewModel` appelaient leur méthode
   `RunXAsync()` DIRECTEMENT depuis `OnNavigatedToAsync()`, hors du garde
   `AsyncRelayCommand.IsRunning` que le bouton manuel utilisait — un clic
   pendant le chargement automatique déclenchait une deuxième exécution
   concurrente, chacune écrivant `Result`/l'état de session indépendamment.
   Corrigé en routant `OnNavigatedToAsync()` par la commande elle-même
   (`RecheckCommand.ExecuteAsync()` etc.) — un seul garde partagé, quelle
   que soit l'origine du déclenchement. Test :
   `Concurrent_automatic_and_manual_recheck_never_run_two_checks_at_once`
   (compte les VRAIS appels Runtime pour prouver qu'une seule exécution a
   eu lieu).
4. **Fermer la fenêtre pendant une installation abandonnait la tâche sans
   avertissement** — aucun gestionnaire `Closing` n'existait ; fermer via
   le bouton OS pendant `InstallCommand.IsRunning` tuait la fenêtre (et le
   process) immédiatement, sans jamais signaler l'annulation au
   `CancellationToken` déjà propagé jusqu'au pipeline — risque réel de
   verrou/état orphelin. Corrigé (`MainWindow.OnClosing`) : la première
   tentative de fermeture pendant une installation est bloquée et
   déclenche une annulation RÉELLE et coopérative
   (`InstallProgressViewModel.RequestCancel`, jamais un `Process.Kill`) ;
   la fenêtre se ferme elle-même dès que l'installation s'est
   effectivement arrêtée.

Chaque exécution `RunXAsync` porte désormais aussi un `catch (Exception)`
générique (au-delà du seul écran Install) — même discipline que le CLI
(`Program.Main`'s `catch (Exception ex) → EmitInternalError`), pour que le
GUI ne puisse ni planter ni faire disparaître une erreur silencieusement
sur AUCUN des 9 écrans.

## Limite assumée de cet environnement

Ce sandbox est Linux, sans serveur d'affichage permanent. Le lancement
réel du binaire compilé a été vérifié sous `Xvfb` (fenêtre affichée
plusieurs secondes, aucune exception, arrêt propre) — preuve que
l'application démarre réellement, au-delà des seuls tests unitaires de
ViewModel. Une validation visuelle interactive complète (rendu pixel par
pixel, comportement tactile/souris fin) n'est pas possible ici et n'a pas
été prétendue — voir Phase 9 pour la déclaration honnête
TESTED/NOT TESTED.
