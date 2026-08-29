# Corrections K1 et K2

Deux défauts détectés par la première exécution réelle du Gate (machine #1,
WSL2, Ubuntu 26.04, Python 3.14.4). Aucun n'avait été vu par les 134 tests
existants : ils ne se manifestent que sur une installation neuve.

---

## K1 — GATE-11 rapportait un faux DEGRADED

**Pourquoi le défaut existait.** Le contrôle mesurait directement
`paths.data_root` (`/home/apex/apex-data`). Ce répertoire est créé à l'usage,
pas à l'installation : sur une machine neuve il n'existe pas, `shutil.disk_usage`
lève `OSError`, et le contrôle concluait « espace libre indéterminé ». La
machine pouvait disposer de 500 Go libres.

Le défaut était donc systématique sur toute machine neuve, y compris la
machine cible.

**Comportement corrigé.** `Probe.disk_probe()` remonte au premier ancêtre
existant. C'est le même point de montage, donc la mesure est valide pour le
futur chemin. **Aucun répertoire n'est créé** — la sonde reste en lecture
seule.

Quatre situations restent distinctes, car elles n'appellent pas la même action :

| Situation | Statut | Faits publiés |
|---|---|---|
| Chemin cible existant, mesuré | `PASS` / `DEGRADED` selon le seuil | `measured_on_ancestor: false` |
| Chemin absent, ancêtre mesuré | `PASS` / `DEGRADED` selon le seuil | `measured_on_ancestor: true`, `path_measured` |
| Erreur système (permissions…) | `DEGRADED` | `disk_error` avec le type d'exception |
| Aucun système de fichiers mesurable | `UNKNOWN` (`unmeasurable`) | `disk_free_gb: null` |

Le contrôle publie toujours `path_requested` et `path_measured` : on sait sur
quoi porte la mesure.

**Limite connue.** Si `data_root` finit sur un point de montage différent de
son ancêtre (montage créé ultérieurement), la mesure porterait sur le mauvais
système de fichiers. Le cas est rare et le rapport indique le chemin mesuré,
ce qui le rend détectable.

---

## K2 — `docker-compose.yml` sur-allouait la RAM

**Pourquoi le défaut existait.** Le fichier déclarait 6 Go (postgres) + 1,5 Go
(redis) + 2 Go (minio) = **9,5 Go** de limites mémoire, pour une machine
exposant **6,7 Go** à WSL2. `docker compose up` aurait échoué ou déclenché
l'OOM killer, avec un diagnostic sans rapport apparent avec la cause. Aucun
contrôle ne confrontait les deux.

Second défaut, plus insidieux : les limites n'étaient déclarées que via
`deploy.resources.limits`, qui n'est honoré que par **Compose V2 hors Swarm**.
`docker-compose` V1 (Python) l'ignore **silencieusement**. Sur un tel runtime,
les trois services auraient tourné sans aucun plafond — le pire cas pour une
contrainte de sécurité, puisque rien ne le signale.

**Comportement corrigé.**

1. Chaque service déclare `mem_limit` **et** `deploy.resources.limits`, avec
   la même valeur. `mem_limit` est honoré par V1 comme par V2.
2. Les valeurs par défaut sont dimensionnées pour la phase 2 — migrations et
   tests d'intégration — et non pour une charge de production :

   | Service | Avant | Après | Justification |
   |---|---|---|---|
   | postgres | 6 Go | **2 Go** | `shared_buffers` ramené de 1 Go à 256 Mo ; la charge visée n'existe pas encore |
   | redis | 1,5 Go | **512 Mo** | `maxmemory` à 256 Mo ; la file de tâches est petite et `noeviction` interdit de la laisser croître |
   | minio | 2 Go | **1 Go** | Stockage d'artefacts, empreinte mémoire faible |
   | **Total** | **9,5 Go** | **3,5 Go** | |

3. Chaque valeur est surchargeable par variable d'environnement
   (`APEX_PG_MEM`, `APEX_REDIS_MEM`, `APEX_MINIO_MEM`…). La machine cible
   pourra les relever sans modifier le fichier.

Les limites n'ont pas été supprimées ni choisies pour faire passer le Gate :
elles ont été ramenées au besoin réel de la phase 2, et restent paramétrables.

---

## GATE-21 — nouveau contrôle

Confronte la somme des limites mémoire Compose à la RAM visible de l'hôte.

**Politique de calcul.**

```
ratio = somme(limites mémoire déclarées) / RAM visible
```

- Les **réservations** sont exclues : `reservations.memory` réserve à
  l'ordonnancement mais ne plafonne rien, donc ne peut pas sur-allouer.
- Quand les deux formes (`mem_limit` et `deploy`) sont présentes, le plafond
  effectif retenu est **la plus basse**.
- Une valeur mémoire ininterprétable est une **erreur** (`FAIL`), jamais une
  absence de limite : confondre les deux masquerait une faute de frappe.

**Seuils, configurables dans `config.yaml`** (section `gate.compose_memory`,
consommée par le contrôle — ce ne sont pas des valeurs décoratives) :

| Condition | Statut |
|---|---|
| `ratio > fail_ratio` (défaut 1.00) | `FAIL`, bloque la phase 2 |
| Un service sans limite mémoire | `DEGRADED` — le total est une borne inférieure |
| Limites en `deploy` seul, runtime non-V2 | `DEGRADED` — limites potentiellement ignorées |
| `ratio > warn_ratio` (défaut 0.75) | `DEGRADED` — plus assez de marge pour l'hôte |
| Ratio sain, runtime V2 confirmé | `PASS` |
| Ratio sain, Docker absent | `UNKNOWN` (`dependency_missing`) |

**Comportement UNKNOWN.** La sur-allocation est une propriété du fichier et de
la machine : elle se calcule sans Docker, et un `FAIL` arithmétique est rendu
même si Docker est absent. En revanche, savoir si les limites seront
**effectivement appliquées** exige de connaître le runtime. Sans Docker, cette
question reste `UNKNOWN` — jamais `PASS`. Le contrôle ne prétend pas qu'une
limite est active s'il ne peut pas le vérifier.

**Compatibilité Compose.** Le contrôle identifie `v2` (via
`docker compose version --short`), `v1` (binaire `docker-compose` seul), ou
`indéterminé`. Le fait `limits_enforced` vaut `true`, `false` ou `null` — et
`null` signifie inconnu, pas absent.

**Ce que le contrôle ne fait pas.** Il lit un fichier. Aucun conteneur n'est
démarré, aucune image tirée, aucune modification de l'hôte. Un test le vérifie.

---

## Nouveau motif UNKNOWN

`dependency_missing` rejoint `not_authorized`, `not_applicable` et
`unmeasurable`. Il distingue « un outil requis pour mesurer est absent » d'un
refus d'autorisation ou d'une impossibilité définitive — trois situations qui
appellent trois actions différentes.
