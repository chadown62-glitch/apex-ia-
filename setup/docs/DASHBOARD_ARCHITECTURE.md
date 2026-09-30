# Panel APEX (`APEX.exe`) — Architecture

Mis à jour par la mission « Desktop Distribution » (voir `docs/DESKTOP_DISTRIBUTION.md` pour l'audit
et les décisions, `docs/INSTALLER_APEX.md` pour l'utilisateur).

## Rôle

Observateur. Le Panel mesure et affiche l'état réel d'APEX ; il ne démarre **jamais** WSL, le
backend ni l'interface web. Le backend reste lancé par `scripts/apex-launch.sh serve` dans WSL.
(L'ancien orchestrateur LANCER / ARRÊTER / RÉPARER, qui démarrait WSL, `apex-launch.sh serve` et
`npm run dev`, a été retiré ; `ApexSetup.Launcher` reste le lanceur headless distinct.)

## Chaîne de mesure

```
PanelStatusService.MeasureAsync
  1. IBackendHost.ProbeAsync            Windows : wsl.exe -l -v + wsl.exe -l --running -q
                                        (ne démarrent rien ; état lu sans aucun mot traduit)
                                        Linux : hôte local
     ├─ distribution choisie dans Paramètres -> elle seule
     ├─ sinon : la distribution par défaut si elle tourne, puis les AUTRES déjà en cours ; la
     │  première qui a un enregistrement de découverte est retenue et affichée
     └─ aucune candidate en cours -> STOP : aucune autre commande (rien ne la démarrerait)
  2. ~/.apex/runtime/backend.json       enregistrement de découverte (apex/discovery.py)
  3. /proc/<pid>/cmdline                le processus annoncé vit-il ?
  4. <projet>/.apex/api-token           en mémoire seulement, masqué dans les journaux
  5. GET /api/v1/status                 StatusClient : refus / délai / 401 / 404 ancien backend /
                                        autre programme / réponse valide
StatusEngine.Decide (fonction pure)  ->  PanelSnapshot (états + verdict + consigne)
```

Sous Windows, chaque lecture est `wsl.exe [-d D] [--cd ~] --exec cat -- <chemin>` : argv direct,
jamais le shell Linux ; chemins validés (`HostPaths`).

## Vocabulaire

Composants : WSL (Windows seulement), Backend, API, Database, Redis, Ollama, Sandbox, Brain.
États : READY, STARTING, OFFLINE, DEGRADED, ERROR, UNKNOWN. Verdict : APEX READY / APEX DEGRADED /
APEX NOT READY (cause précise, `NotReadyCause`) / VERSION_MISMATCH (protocole Panel/backend,
`StatusEngine.SupportedProtocol` = `PANEL_PROTOCOL` de `apex/version.py`).

Règles : READY uniquement sur une réponse authentifiée au bon protocole ; un service derrière un
backend injoignable est UNKNOWN ; un enregistrement de découverte ne prouve rien (il distingue
seulement « en démarrage » / « arrêté ») ; un protocole inconnu n'est jamais interprété.

## Fichiers

| Quoi | Où |
|---|---|
| Programme | `%LOCALAPPDATA%\Programs\APEX` (installateur par utilisateur) |
| Paramètres (facultatifs : distribution WSL) | `%APPDATA%\APEX\dashboard-settings.json` |
| Journaux | `%LOCALAPPDATA%\APEX\logs\panel-AAAAMMJJ.log` (14 jours, secrets masqués) |
| Secrets | aucun côté Windows |

## Autres garanties

Instance unique (mutex `APEX.Panel.SingleInstance`, partagé avec l'`AppMutex` de l'installateur ;
un second lancement active la fenêtre existante). Plantage journalisé. `--status-json`,
`--diagnostic`, `--version`, `--software-rendering`. Rafraîchissement automatique toutes les 5 s,
jamais deux mesures en parallèle.

## Tests

`setup/tests/ApexSetup.Dashboard.Tests` (moteur d'état, client HTTP contre un vrai serveur local,
hôtes, journaux, diagnostic, paramètres, instance unique, vues-modèles). Le transport `wsl.exe`
n'est testé qu'avec un double (MOCK) : il n'existe pas dans l'environnement de développement.
