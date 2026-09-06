# Contrats JSON — `apex doctor --json` / `apex gate --json`

Ce document fixe la frontière machine-lisible entre un futur **APEX Setup**
et le Runtime : `apex doctor --json` et `apex gate --json` sont conçus pour
être exécutés en sous-processus, jamais en important `apex.doctor`/`apex.gate`
directement. Un consommateur (Setup, ou tout autre outil, y compris non
Python) lit uniquement leur sortie standard JSON.

```
APEX Setup
    │  sous-processus, lit stdout
    ▼
apex doctor --json   apex gate --json
    │                      │
    ▼                      ▼
 décision d'installation / configuration
```

**Principe directeur** : ce sont **deux contrats distincts**, jamais
fusionnés. Doctor et Gate ne mesurent pas la même chose :

| | Doctor | Gate |
|---|---|---|
| Question posée | « cet environnement, à l'instant présent, est-il sain ? » | « cette machine autorise-t-elle le passage à la phase suivante ? » |
| Vocabulaire de statut | `PASS` / `WARN` / `FAIL` / `SKIP` | `PASS` / `FAIL` / `DEGRADED` / `UNKNOWN` / `SIMULATED` |
| Persisté sur disque ? | non | oui (`.apex/environment.json`, archivé par empreinte) |
| Empreinte matérielle | non | oui (`fingerprint`, compare les mesures entre machines) |

Unifier ces deux vocabulaires ferait dire à l'un des deux outils une chose
qu'il ne mesure pas réellement (ex. faire correspondre `WARN` à `DEGRADED`
serait une invention, pas une mesure) — délibérément évité.

---

## Doctor JSON Contract

Produit par `apex.doctor.runner.DoctorReport.to_dict()`, imprimé par
`apex doctor --json`. Le mode texte (`apex doctor`, sans `--json`) reste
strictement inchangé par ce contrat — les deux sont deux représentations
du même `DoctorReport`, jamais deux calculs séparés.

```json
{
  "schema_version": 1,
  "apex_version": "0.1.2",
  "generated_at": "2026-09-03T12:00:00Z",
  "status": "fail",
  "counts": {"pass": 8, "warn": 4, "fail": 5, "skip": 4},
  "duration_ms": 147.0,
  "results": [
    {
      "name": "python.version",
      "category": "environnement",
      "severity": "pass",
      "message": "Python 3.12.3",
      "remediation": "",
      "facts": {"version": "3.12.3"},
      "duration_ms": 0.4
    }
  ]
}
```

### Champs

| Champ | Type | Obligatoire | Détail |
|---|---|---|---|
| `schema_version` | entier | oui | voir §Versionnage |
| `apex_version` | chaîne | oui | `apex.version.__version__` — la version du dépôt qui a produit ce rapport |
| `generated_at` | chaîne | oui | voir §Déterminisme et horodatage |
| `status` | chaîne | oui | le pire statut parmi `results[].severity` — un des 4 valeurs ci-dessous |
| `counts` | objet | oui | nombre de résultats par statut, les 4 clés toujours présentes (0 si absent) |
| `duration_ms` | nombre | oui | durée totale de la collecte, arrondie à 1 décimale |
| `results` | tableau | oui | un élément par check exécuté, jamais vide sauf configuration de checks explicitement réduite |
| `results[].name` | chaîne | oui | identifiant stable du check (ex. `python.version`) — clé de référencement, jamais renommé sans bump majeur |
| `results[].category` | chaîne | oui | regroupement d'affichage (`environnement`, `stockage`, `conteneurs`, `gpu`, `configuration`, `services`) |
| `results[].severity` | chaîne | oui | `pass` \| `warn` \| `fail` \| `skip` — jamais une 5ᵉ valeur sans bump majeur |
| `results[].message` | chaîne | oui | texte humain, jamais vide |
| `results[].remediation` | chaîne | non (peut être vide) | conseil de correction ; chaîne vide si sans objet (ex. déjà `pass`) |
| `results[].facts` | objet | non (peut être `{}`) | données structurées propres au check — **forme libre par check, jamais garantie stable entre checks** ; un consommateur ne doit lire que les clés qu'il connaît déjà, jamais itérer aveuglément dessus pour une décision |
| `results[].duration_ms` | nombre | oui | durée de CE check |

### Statuts (`results[].severity`, `status`)

- `pass` — mesuré, conforme.
- `warn` — mesuré, fonctionne mais en dehors des bonnes pratiques ou sur un repli ; ne bloque jamais une phase.
- `fail` — mesuré, bloquant. Inclut les erreurs d'exécution du check lui-même (`category: "interne"`, `run_checks` ne laisse jamais une exception non capturée — voir `apex/doctor/runner.py::run_checks`) : un Setup qui voit `category == "interne"` sait qu'il s'agit d'une anomalie du diagnostic lui-même, pas d'un défaut de la machine.
- `skip` — vérification non applicable dans ce contexte (dépendance absente, autre OS).

`status` (niveau racine) est le pire de ces quatre selon l'ordre `skip < pass < warn < fail`.

---

## Gate JSON Contract

**Aucun changement dans cette phase.** Déjà mature, versionné et testé —
voir `apex/gate/report.py::EnvironmentReport.to_dict()` (implémentation) et
`docs/PHASE_1_GATE.md` (description narrative déjà existante). Rappel des
champs de premier niveau, pour référence croisée avec ce document :
`schema_version` (=1, compteur propre à Gate, jamais couplé à celui de
Doctor), `apex_version`, `generated_at`, `probe`, `machine_label`,
`measured_on_target_machine`, `gate_status`
(`PASS`/`FAIL`/`DEGRADED`/`UNKNOWN`/`SIMULATED`), `phase_2_authorized`,
`fingerprint`, `counts`, `capabilities`, `hardware`, `blocking`,
`unresolved`, `exceptions`, `expired_exceptions`, `missing_dependencies`,
`checks[]`.

---

## Versionnage

Chaque contrat porte son propre `schema_version` entier, commençant à `1`,
**incrémenté indépendamment l'un de l'autre** — une évolution du contrat
Doctor n'implique jamais un changement du contrat Gate, et réciproquement.

**Reste à la même version (changement additif, compatible)** :
- ajout d'une nouvelle clé de premier niveau ;
- ajout d'une nouvelle clé dans `results[]`/`checks[]` ;
- ajout d'un nouveau `name`/`check_id` (un nouveau check) ;
- ajout d'une clé dans `facts`/`capabilities`/`hardware` (déjà à forme libre).

Un consommateur correctement écrit ignore les clés qu'il ne connaît pas —
c'est la règle de compatibilité qu'il doit respecter en retour.

**Exige un `schema_version` incrémenté (changement majeur, potentiellement incompatible)** :
- retrait ou renommage d'une clé de premier niveau ou de `results[]`/`checks[]` ;
- changement de type d'une clé déjà publiée (ex. `duration_ms` cesse d'être un nombre) ;
- changement de sens d'une clé déjà publiée ;
- retrait d'une valeur de vocabulaire (`severity`/`status`) déjà publiée ;
- changement de la sémantique de `status`/`gate_status` (ce qui compte comme pire statut).

Un Setup qui lit un `schema_version` **supérieur** à celui qu'il connaît doit
refuser de décider automatiquement à partir de ce rapport (dégrader vers une
confirmation manuelle) plutôt que de deviner — même discipline que
`GateStatus.UNKNOWN` : une version non reconnue est une inconnue, jamais une
supposition de compatibilité.

---

## Déterminisme et horodatage

`generated_at` (Doctor et Gate) : chaîne ISO 8601, toujours en **UTC**,
toujours suffixée `Z`, format exact `%Y-%m-%dT%H:%M:%SZ` (ex.
`2026-09-03T12:00:00Z`) — pas de microsecondes, pas de décalage local.
**Toujours présent** (jamais optionnel) — un rapport a nécessairement un
instant de génération. En cas d'échec de formatage (ne devrait
structurellement jamais arriver, `time.strftime` sur un flottant Unix valide
ne lève pas), le comportement est celui, déjà existant, de `run_checks` pour
toute exception non prévue : `apex doctor`/`gate` ne plante jamais un
diagnostic sur une anomalie interne, elle devient un résultat `fail`
catégorie `interne`.

**Pour les tests et l'automatisation** : ne jamais comparer `generated_at` à
une valeur exacte (non déterministe par construction — c'est un horodatage
réel). Vérifier son **format** (regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`)
et, si nécessaire, son **ordre relatif** (généré après le début de la
collecte), jamais sa valeur littérale.
