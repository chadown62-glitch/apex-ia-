# Setup — Packaging Windows (Phase 6)

## Stratégie

`setup/packaging/publish.sh` produit, pour `ApexSetup.Cli`,
`ApexSetup.Gui` et (N+15) `ApexSetup.Launcher`, un binaire **win-x64
self-contained** :

- **Self-contained** : le runtime .NET est embarqué — un installeur ne
  doit jamais supposer que la machine cible a déjà .NET installé.
- **Fichier unique pour le CLI** (`PublishSingleFile=true`) : un seul
  `apex-setup.exe` distribuable/scriptable facilement (CI, ligne de
  commande). Le GUI reste multi-fichiers (Avalonia recommande cette forme
  pour les applications desktop — les DLL natives Skia/ANGLE ne se prêtent
  pas bien à l'auto-extraction single-file). Le Launcher (N+15) reste
  également multi-fichiers : il cohabite dans le même répertoire de
  release que `ApexSetup.Gui.exe`, qu'il invoque directement pour
  installation/réparation (voir `LAUNCHER_ARCHITECTURE.md`) — un
  fichier unique n'apporterait aucun bénéfice à cette cohabitation.
- **Versionné** : `Directory.Build.props` (`SetupProductVersion`) fixe une
  version UNIQUE pour tout `setup/` — CLI, GUI, Launcher et le manifeste de
  release (Phase 7) rapportent tous exactement le même numéro,
  jamais un `1.0.0.0` par défaut d'assembly non versionnée (voir test de
  régression `Version_reports_the_centrally_versioned_setup_version_never_a_default_placeholder`).
- **Reproductible autant que possible** : `dotnet publish` avec un
  `Configuration=Release` figé, sans horodatage ni GUID dans le nom du
  binaire — seule la version (explicite, dans le nom du répertoire de
  sortie) varie. La reproductibilité BIT-À-BIT complète n'est PAS
  garantie (le compilateur .NET n'est pas encore parfaitement
  déterministe pour tous les artefacts natifs AOT-liés d'un publish
  self-contained) — non affirmée au-delà de ce qui est vérifié.

## Vérifié réellement (pas seulement supposé)

Ce dépôt n'a pas de machine Windows disponible pour exécuter le résultat
— mais la PRODUCTION du binaire, elle, est intégralement vérifiable
depuis Linux et l'a été :

- `file apex-setup.exe` → `PE32+ executable (console) x86-64, for MS
  Windows` (CLI) et `PE32+ executable (GUI) x86-64, for MS Windows` (GUI)
  — un binaire Windows RÉEL, pas une affirmation.
- Testé avec un chemin de sortie contenant espaces ET caractères Unicode
  (`/tmp/déploiement avec espaces/dist`) — succès, aucune dépendance
  implicite à un chemin ASCII sans espace.
- **Bug trouvé et corrigé pendant cette phase** : le premier build Release
  embarquait le chemin ABSOLU du PDB de la machine de développement
  (`/home/user/apex-ia-/setup/...`) dans le binaire distribué —
  découvert via `strings apex-setup.exe`, jamais supposé absent. Corrigé
  par `Directory.Build.props` (`DebugType=none` en configuration Release)
  — aucun `.pdb` n'est plus généré ni distribué. `publish.sh` revérifie
  ceci à CHAQUE publication (`strings` + recherche de chemins absolus,
  `find -name '*.pdb'`), jamais une seule fois puis oublié.

## Ce qui N'EST PAS fait dans cette phase (honnêteté explicite)

- **Aucun `.msi`/`.exe` installeur** (WiX Toolset, Inno Setup, ou
  équivalent) n'est généré — ces outils nécessitent Windows (ou une
  intégration CI Windows) pour produire un installeur testé de façon
  fiable ; les construire à l'aveugle depuis Linux, sans jamais pouvoir
  vérifier une installation/désinstallation réelle, aurait été une
  affirmation non vérifiée. `publish.sh` produit la matière première
  (binaires + structure de répertoire) qu'un futur pipeline CI Windows
  empaquetterait.
- **Aucune signature de code** — aucun certificat de signature
  disponible dans cet environnement (règle explicite : ne jamais
  inventer une signature). `SignAssembly=false` documenté explicitement
  dans `Directory.Build.props` ; la structure produite est prête à être
  signée (`signtool sign /f cert.pfx apex-setup.exe`) le jour où un
  certificat existe — infrastructure, pas signature.
- **Désinstallation** : sans installeur MSI réel, il n'existe pas encore
  de mécanisme de désinstallation dédié — Setup lui-même n'écrit rien
  hors du répertoire de destination choisi par l'utilisateur (voir
  `PathSafety`, Phase 2), donc supprimer ce répertoire suffit
  aujourd'hui ; un vrai désinstalleur Windows (entrée « Programmes et
  fonctionnalités ») reste un travail futur explicitement hors périmètre.
