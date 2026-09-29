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

## Installateur Windows du Panel (mission Desktop Distribution)

Mise à jour : l'installateur EXISTE désormais et est testé autant que cet environnement le permet
(voir `docs/DESKTOP_DISTRIBUTION.md`).

- `packaging/build_desktop_release.sh` : publication du Panel (`publish_dashboard.sh`, désormais
  **multi-fichiers** — le fichier unique ne démarre pas sous Wine, mesuré), compilation de
  `ApexSetup.iss` par Inno Setup 6.4.3 (figé par empreinte, `fetch_innosetup.sh`, exécuté par Wine
  sous Linux ou directement sous Windows via `ISCC=`), puis `APEX-Setup-x64-v<version>.exe`,
  `.sha256`, `manifest.json`, `build.log`. Dates des fichiers ramenées à `SOURCE_DATE_EPOCH` :
  deux builds du même commit donnent le **même SHA256** (mesuré, dont un build propre).
- Installation **par utilisateur, sans administrateur** (`%LOCALAPPDATA%\Programs\APEX`), raccourcis
  Bureau et menu Démarrer automatiques, validation post-installation, désinstallation qui conserve les
  données utilisateur et ne touche jamais WSL.
- `packaging/wine_installer_e2e.sh` : cycle de vie complet sous Wine (WINE E2E — jamais présenté
  comme une preuve Windows).

## Toujours non fait (honnêteté explicite)

- **Aucune signature de code** — aucun certificat disponible (règle : ne jamais inventer une
  signature). Conséquence : avertissement SmartScreen au premier lancement.
- **Aucune validation sur un Windows réel** dans cet environnement : WSL, redirection localhost
  WSL2 -> Windows, raccourcis du vrai shell, redémarrage : NOT VERIFIED.
- `.msi` : non produit (Inno Setup `.exe` retenu, déjà présent dans le dépôt).
