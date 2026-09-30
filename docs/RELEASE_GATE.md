# APEX Desktop 0.2.2 — Release gate (Windows / WSL2)

Date : 2026-09-30. Code validé : commit `6edb965` (branche `claude/apex-official-development-4kao8t`).
Preuve principale : workflow `windows-desktop`, **run 12** (id 36729749098, job `real-windows` 109935885840).

Étiquettes de preuve (jamais confondues) :

| Étiquette | Signification |
|---|---|
| **REAL WINDOWS SERVER** | runner GitHub `windows-latest` = Windows Server 2025 Datacenter 10.0.26100, exécuté en administrateur |
| **REAL WSL2** | vraie distribution Ubuntu-24.04 sous WSL 2 sur ce même runner, vrai `launch.sh`, vrai Docker |
| **REAL WINDOWS 10/11 CLIENT** | aucun résultat à ce jour |
| **USER PC NOT VERIFIED** | rien n'a été exécuté sur le PC de l'utilisateur |

Aucune simulation (Wine, faux `wsl.exe`) n'est comptée ci-dessous comme preuve Windows.

## 1. Verdict

- **REAL WINDOWS SERVER + REAL WSL2 : 42 PASS, 0 FAIL, 5 BLOCKED** (run 12).
- **Release Windows 10/11 : BLOCKED** — `release_gate.py` n'accepte comme PASS qu'un résultat obtenu sur un
  Windows 10/11 client ; il n'en existe aucun. Le kit de validation est prêt pour le PC de l'utilisateur.

## 2. Gates A–N

| Gate | Statut | Preuve / raison |
|---|---|---|
| A Build | **PASS** | Installateurs 0.2.2 et 0.2.1 compilés nativement sur Windows (ISCC, run 12) ; manifeste : installateur `b0db6887…94f0`, 213 fichiers, sans modification non commitée, non signé |
| B Installer | **PASS** (Server) | I01–I14 : installation silencieuse sans admin (HKCU), 215 fichiers, raccourcis Bureau/Menu Démarrer, 2ᵉ installation, refus Panel ouvert, installation interrompue puis reprise, désinstallation (journaux conservés), réinstallation, mise à jour 0.2.1→0.2.2. **I00 BLOCKED** : le runner est administrateur, l'absence d'élévation n'y est pas prouvable. **X01 BLOCKED** : SmartScreen exige un humain et un fichier téléchargé |
| C PowerShell 5.1 | **PASS** | Analyse par Windows PowerShell 5.1.26100 lui-même + PSScriptAnalyzer (cible 5.1) ; script exécuté de bout en bout sous 5.1. BOM exigé (défaut réel du run 8, test de régression) |
| D Détection WSL | **PASS** (Server) | W01–W04 : distribution, WSL arrêté jamais redémarré par le Panel (30 s), distribution inexistante, backend absent. **W05 BLOCKED** : une seule distribution sur le runner |
| E Lancement WSL | **PASS** (REAL WSL2) | Vrai `launch.sh serve` : STARTING vu (B02), backend prêt (B03), relance après crash (B09), interruption pendant le démarrage (B11). Voir §4 : Gate APEX excusé en CI |
| F Docker/services | **PASS** (REAL WSL2) | `docker compose up` postgres+redis, `apex doctor` sans FAIL. **B04 BLOCKED** : pas READY sur le runner (Ollama absent, sandbox sans isolation réelle, cerveau non configuré) — le Panel l'affiche DEGRADED, conforme au backend (B05 : aucun écart) |
| G Démarrage backend | **PASS** (REAL WSL2) | DEGRADED en 47 s depuis Windows par localhost (B03) ; après crash : 29 s (B09) |
| H Cycle de vie APEX.exe | **PASS** (Server) | L01–L05 : fenêtre en 1,1 s, instance unique, Menu Démarrer, relance après kill, fermeture sans orphelin ; S01 : aucun enfant non autorisé ; S02 : options hostiles sans effet ; P01 : 2,3 % d'un cœur, 118 Mo |
| I Doctor / diagnostic | **PASS** | `--diagnostic` UTF-8 sans secret (I08) ; bouton « Copier le diagnostic » via UI Automation, 8 secrets recherchés, 0 trouvé (S03) ; `apex doctor` : MinIO absent = WARN « non bloquant » |
| J Lancement (double-clic) | **PASS** (Server) / **BLOCKED** (parcours utilisateur) | Raccourci Bureau → fenêtre (L01), Panel ouvert après/avant le backend (B06, B13). **U01 BLOCKED** en CI : exige un backend lancé par l'utilisateur lui-même |
| K Arrêt / redémarrage | **PASS** (processus) / **NOT RUN** (Windows) | Arrêt propre Ctrl+C (B10), kill -9 → BackendStopped (B08), figé → ApiTimeout puis rétabli (B07). **R01–R04 NOT RUN** : un runner hébergé ne peut pas redémarrer au milieu d'un job |
| L Script de validation | **PASS** | Exécution complète sous PS 5.1, `results.json` + archive masquée produits, code 1 sur tout FAIL. Défauts réels du script corrigés aux runs 8–11 (voir §3) |
| M MinIO facultatif | **PASS** | M01 : backend DEGRADED sans aucune image MinIO, conteneur `apex-minio` absent. Gate M : image construite depuis les sources officielles (CI uniquement), `COMPOSE_PROFILES=minio` → conteneur *healthy*, arrêt, volume `apex_minio-data` conservé |
| N Tests de non-régression | **PASS** (avec échecs préexistants) | pytest : 2627 passed, 175 skipped, **4 failed** — `tests/test_runtime_packaging.py` (une migration référence `apex.metalearner`), identiques avant et après ces changements, hors périmètre Desktop. .NET : 496/496 (aucun C# modifié depuis) |

## 3. Historique CI (workflow `windows-desktop`, REAL WINDOWS SERVER)

| Run | Commit | Résultat | Cause réelle | Correction |
|---|---|---|---|---|
| 8 | — | script illisible | `.ps1` UTF-8 sans BOM lu en Windows-1252 par PS 5.1 | BOM + test + analyse par PS 5.1 en CI |
| 9 | — | script interrompu | `.Count` sur objet unique, `$script:KeepAlive` non initialisé (StrictMode) : ni zip ni tests backend | StrictMode 1, variables initialisées, groupes isolés |
| 10 | — | idem + faux positifs | S02 comptait la fenêtre ouverte par une option inconnue comme blocage ; I01 PASS alors que runner admin | S02 corrigé ; I00 BLOCKED en admin |
| 11 | f694541 | 32 PASS / 10 FAIL / 5 BLOCKED | `launch.sh` arrêté par `apex gate` non concluant (code 5) : backend jamais démarré ; S05 : faux serveur du test en course sur le port | refus du préflight = BLOCKED avec la cause ; S05 réécrit ; Gate excusé en CI (§4) |
| 12 | 6edb965 | **42 PASS / 0 FAIL / 5 BLOCKED** | — | — |

## 4. Écart assumé en CI : dérogations du Gate APEX

`launch.sh serve` ne démarre APEX que si `apex gate` est concluant. `launch.sh` n'autorise jamais les sondes
réseau, conteneur et bureau : GATE-15/17/18/22 sont donc UNKNOWN (`not_authorized`) **sur toute machine**, et
GATE-08/09/10 le sont sans GPU (`not_applicable`). En CI seulement, `prepare_wsl_backend.sh` écrit un
`gate.exceptions.yaml` (mécanisme officiel, `gate.exceptions.example.yaml`) pour ces seules raisons, jamais
pour un FAIL, expirant le lendemain (`gate_exceptions_for_ci.py`, testé). Rien n'est changé dans APEX.

**Décision en attente du propriétaire** : sur un poste sans `gate.exceptions.yaml`, `launch.sh serve` s'arrête
sur « Phase 2 autorisée : NON » ; le Panel affiche alors « démarre… » puis « non démarré ». Faut-il que
`launch.sh` traite différemment les UNKNOWN `not_authorized` qu'il provoque lui-même ? Non modifié.

## 5. Matrice de preuves

| Capacité | Linux (tests / CI Linux) | Windows Server (REAL, run 12) | Windows 10/11 client | PC utilisateur |
|---|---|---|---|---|
| Build installateur | PASS (Wine, mission précédente) | PASS (natif) | NOT RUN | NOT VERIFIED |
| Installation sans admin | — | PASS (HKCU) ; élévation non prouvable (admin) | NOT RUN | NOT VERIFIED |
| Désinstallation / réinstallation / mise à jour | — | PASS | NOT RUN | NOT VERIFIED |
| SmartScreen | — | BLOCKED | NOT RUN | NOT VERIFIED |
| Script PS 5.1 | parse (pwsh 7) | PASS (5.1 réel) | NOT RUN | NOT VERIFIED |
| Détection WSL (registre, codes de sortie) | tests unitaires (sorties réelles capturées) | PASS | NOT RUN | NOT VERIFIED |
| `launch.sh` réel dans WSL2 | tests launcher | PASS (REAL WSL2) | NOT RUN | NOT VERIFIED |
| Backend prêt vu depuis Windows (localhost WSL2) | — | PASS (DEGRADED) | NOT RUN | NOT VERIFIED |
| APEX READY complet | — | BLOCKED (Ollama/sandbox/cerveau absents) | NOT RUN | NOT VERIFIED |
| Crash / figé / recovery / arrêt | tests StatusEngine | PASS | NOT RUN | NOT VERIFIED |
| Sécurité (secrets, options, découverte hostile, port) | tests unitaires | PASS | NOT RUN | NOT VERIFIED |
| Parcours utilisateur (U01) | — | BLOCKED | NOT RUN | NOT VERIFIED |
| Redémarrage Windows (R01–R04) | — | NOT RUN | NOT RUN | NOT VERIFIED |
| MinIO facultatif | PASS (compose/doctor réels) | PASS (M01 + Gate M) | NOT RUN | NOT VERIFIED |

## 6. Kit de validation pour le PC

Artefact `APEX-validation-kit` du run 12 (construit depuis `6edb965`) :
https://github.com/chadown62-glitch/apex-ia-/actions/runs/36729749098/artifacts/11107440994
(archive : sha256 `06953fe0bb3bc737dd34d7e7fc7ec6d61809bc89053cd9cabdebaec16c0ba347`).

Contenu : `APEX-Setup-x64-v0.2.2.exe` (+ `.sha256`), `APEX-Setup-x64-v0.2.1.exe` (test de mise à jour),
`manifest.json`, `Test-ApexWindows.ps1`, `Valider-APEX.cmd`, `LISEZ-MOI.txt`. Aucun secret, jeton ni mot de passe.

Procédure (détail dans `LISEZ-MOI.txt`) :
1. WSL : `bash scripts/apex-launch.sh serve` dans le dossier APEX, fenêtre laissée ouverte ;
2. double-clic sur `Valider-APEX.cmd`, réponses O/N ;
3. accepter ou non le test de redémarrage (reprise automatique après connexion) ;
4. transmettre l'archive `APEX-validation-<date>.zip` créée sur le Bureau (secrets et nom d'utilisateur masqués).
