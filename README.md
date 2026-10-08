# APEX

Plateforme locale d'intelligence artificielle évolutive pour le développement
logiciel. Programme en 22 phases, avec porte qualité entre chacune.

**État : phase 1 terminée — dépôt, configuration, conteneurs, `apex doctor`.**
Aucune capacité d'agent, de sandbox ou d'évolution n'existe encore. Voir
`docs/PHASE_1_REPORT.md` pour l'inventaire exact de ce qui est implémenté,
stub ou planifié.

## Installation (WSL2)

```bash
# 1. Placer le dépôt dans le système de fichiers ext4 de WSL2.
#    JAMAIS sous /mnt/c/ : les E/S y sont ralenties d'un ordre de grandeur
#    sur les arborescences à nombreux petits fichiers.
cd ~ && git clone <url> apex && cd apex

# 2. Configurer WSL2 (voir scripts/wsl/)
#    - .wslconfig  -> %UserProfile%\.wslconfig   (hôte Windows)
#    - wsl.conf    -> /etc/wsl.conf              (dans la distribution)
#    puis, depuis Windows : wsl --shutdown

# 3. Secrets et matériel
#    Chargé automatiquement par `apex` (toute commande) ET par
#    `docker compose` — les deux voient les mêmes valeurs, aucun export
#    manuel de variable d'environnement n'est requis.
cp .env.example .env && $EDITOR .env

# 4. Infrastructure
docker compose up -d

# 5. Diagnostic
python -m apex.cli.main doctor
```

## Lancement (Windows)

Le produit Windows est **APEX.exe** : installateur `releases/windows/APEX-Setup-x64-v<version>.exe`
(voir `docs/INSTALLER_APEX.md`). Depuis 0.3.0, APEX tourne **directement sous Windows**, sans WSL ni
Docker ni terminal (profil autonome, `apex.standalone`) : double-clic, **Préparer APEX** (une fois :
Python officiel et bibliothèques, empreintes vérifiées), **Installer le modèle** (une fois), **Démarrer
APEX** (préflight, `apex doctor`, Startup Gate, jamais contournés), puis **Ouvrir APEX** (l'interface web
servie par APEX lui-même). Reconstruire l'installateur : `bash setup/packaging/build_desktop_release.sh`.

Mode WSL (avancé, profil serveur) : le même Panel pilote `scripts/apex-launch.sh` dans une distribution
Linux. Une fois, dans le dépôt (WSL) : `.env`, `.venv` (ci-dessus), puis l'interface web :

```bash
bash scripts/apex-build-ui.sh
```

`apex.bat` (racine du dépôt) reste un délégateur pur vers `scripts/apex-launch.sh` pour un usage en
invite de commandes. `setup/src/ApexSetup.Launcher` n'est PAS livré et ne doit pas être étendu (voir
l'avertissement en tête de `setup/docs/LAUNCHER_ARCHITECTURE.md`).

## Commandes disponibles

| Commande | Rôle |
|---|---|
| `apex doctor` | diagnostic de l'environnement, **lecture seule** |
| `apex version` | version et informations de build |
| `apex interfaces` | état réel des abstractions : implémenté / stub / planifié |
| `apex config` | configuration validée, telle qu'interprétée |

Les commandes de §52 apparaîtront à la phase où leur sous-système existe.
`apex --help` est une représentation honnête de ce que le système sait faire.

## Principes structurants

- **Core / Genome** — le code est humain, la configuration mutable est de la
  donnée. L'évolution ne touche jamais au Core. (ADR-0001)
- **Rien n'est déclaré avant d'exister** — le registre d'abstractions est
  vérifié par les tests ; il ne peut pas devenir optimiste. (ADR-0007)
- **Fallback 100 % local** — les services externes sont optionnels et bornés
  par contexte ; l'évaluation des promotions est intégralement locale.
  (ADR-0006)
- **La sécurité n'est pas un curseur** — si l'isolation disponible ne suffit
  pas à une fonctionnalité, la fonctionnalité est différée. (ADR-0005)

## Développement

```bash
make test      # suite complète, stdlib uniquement
make lint      # ruff
make types     # mypy
make gate      # porte qualité de fin de phase
```

## Documentation

- `docs/adr/` — décisions d'architecture, une par fichier, jamais réécrites
- `docs/PHASE_1_REPORT.md` — rapport de fin de phase
- Documents de phase 0 : architecture générale et addendum de décisions
