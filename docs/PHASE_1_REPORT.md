# APEX — Rapport de fin de Phase 1

**Phase :** 1 — Dépôt, configuration, conteneurs, `apex doctor`
**Version :** 0.1.0
**Tests :** 61 exécutés, 61 réussis, 0 échec
**Volume :** 3 196 lignes de Python, 47 fichiers

Ce rapport répond au principe verrouillé n°15. Il distingue explicitement ce qui
fonctionne, ce qui n'a pu être que simulé, et ce qui n'existe pas. La section
« simulé » est la plus importante des trois.

---

## 1. Réellement implémenté et testé

| Composant | Détail | Tests |
|---|---|---|
| `apex.errors` | Taxonomie structurée. `NotImplementedYet` porte la phase et le repli ; `IsolationUnavailable` porte l'écart entre isolation requise et disponible | 3 |
| `apex.obs.logging` | Logging JSON, contexte lié, **rédaction des secrets dans le formateur** | 7 |
| `apex.obs.metrics` | Registre thread-safe : compteurs, jauges, histogrammes à buckets, timer ; rendu Prometheus | 4 |
| `apex.obs.health` | Contrat `HealthCheckable`, agrégation, `DEGRADED` comme état de première classe | 2 |
| `apex.util.probe` | Surface d'effets de bord injectable, lecture seule, timeout obligatoire ; `SystemProbe` et `FakeProbe` | couvert indirectement |
| `apex.config` | Schéma complet, substitution `${VAR:-défaut}`, validation des invariants de principes | 17 |
| `apex.interfaces.registry` | 29 abstractions avec état réel, vérifié par test | 6 |
| `apex.doctor` | 20 vérifications, 5 catégories, sorties texte et JSON | 18 |
| `apex.cli` | `doctor`, `version`, `interfaces`, `config` | manuel |

### Invariants réellement appliqués par le code

Ce ne sont pas des intentions documentées : une violation fait échouer le
chargement de la configuration.

- Le Judge ne peut pas appartenir à la même famille de modèles que le coder.
- Les rôles `coder_main`, `judge`, `fast`, `embed` ne peuvent pas être externes.
- Les contextes d'usage externe sont en liste blanche fermée ; toute autre
  valeur est une erreur bloquante.
- La sandbox ne peut déclarer que `network: none` ou `network: proxy`.
- Un pool local est obligatoire.
- Aucun chemin de travail sous `/mnt/c/` (échec bloquant du doctor).
- Le registre d'abstractions ne peut pas déclarer disponible un module absent.

### Bug trouvé et corrigé pendant la phase

`${APEX_EXTERNAL_ENABLED:-false}` produit la chaîne `"false"`, dont la valeur de
vérité Python est `True`. La politique de modèles externes s'activait donc
silencieusement — violation directe du principe 3, invisible dans les journaux.
Découvert au premier essai de bout en bout de la CLI, corrigé par une coercition
booléenne stricte (`as_bool`) où une valeur non reconnue est une erreur et non
un défaut. Quatre tests de régression ajoutés.

C'est exactement la classe de défaut que la porte qualité doit attraper : rien
ne plantait, tout semblait fonctionner.

---

## 2. Simulé — à valider sur la machine cible

**C'est la section critique de ce rapport.** L'ensemble du développement a eu
lieu dans un conteneur Linux sans GPU, sans Docker, sans WSL2 et sans réseau.
Les éléments suivants sont donc corrects *en logique* mais **non vérifiés en
conditions réelles** :

| Élément | Ce qui a été validé | Ce qui ne l'a pas été |
|---|---|---|
| Vérifications WSL2 | Comportement contre `FakeProbe` simulant une machine saine et plusieurs machines dégradées | Le format réel de `/etc/wsl.conf`, la détection du noyau, les valeurs de `nvidia-smi` sous WSL2 |
| Détection Docker | Analyse des sorties de `docker info`, `docker context show` | Aucune commande Docker n'a jamais été exécutée |
| Détection GPU | Parsing de la sortie CSV de `nvidia-smi` | Aucun GPU présent ; format supposé, non observé |
| `docker-compose.yml` | Syntaxe et cohérence des volumes, healthchecks, limites | **Jamais démarré.** Les images n'ont pas été tirées ; les healthchecks n'ont pas été exercés |
| `scripts/init-db/01-extensions.sql` | Syntaxe | Jamais exécuté sur une instance PostgreSQL |
| Catalogue de modèles | Cohérence VRAM déclarée / rôles résidents | Aucun modèle téléchargé ; les tailles VRAM sont des estimations issues de la documentation publique, pas des mesures |
| Fichiers `.wslconfig` / `wsl.conf` | Contenu conforme à la documentation | Jamais appliqués sur un hôte Windows |

**Première action de la phase 2 : exécuter `apex doctor` sur la machine cible
réelle et corriger les écarts.** Il faut s'attendre à ce que plusieurs
vérifications nécessitent un ajustement — les formats de sortie des outils
système sont la source classique de faux positifs.

---

## 3. Stubs et abstractions non implémentées

29 abstractions déclarées. État réel, généré depuis `apex/interfaces/registry.py`
par `scripts/phase_report.py` :

| État | Nombre |
|---|---|
| Implémenté | 2 (`MetricSink`, `HealthCheckable`) |
| Stub | 1 (`EventSink` — logging JSON, persistance PostgreSQL en phase 2) |
| Défini, non implémenté | 2 (`ComputePool`, `LanguageToolchain` — structures de configuration présentes, aucune logique d'usage) |
| Planifié | 24 |

Aucune abstraction planifiée ne possède de module : le test
`test_planned_abstractions_declare_no_module` l'interdit. Aucune abstraction
n'est promise pour la phase 1 sans être implémentée :
`test_phase_one_promises_nothing_it_cannot_keep`.

**Note sur le compte.** Le catalogue de la phase 0 annonçait 23 abstractions ;
il en compte 29 après implémentation. Six ont été ajoutées en découpant des
entrées trop grosses (`Placement` séparé de `ComputePool`, `ToolRegistry` de
`ToolProvider`, `MutationOperator` de `GenomeStore`) et en promouvant trois
contrats transverses (`EventSink`, `MetricSink`, `HealthCheckable`) qui étaient
implicites. Le chiffre de 23 n'était pas faux, il était trop grossier.

---

## 4. Tests

```
Ran 61 tests in 0.008s — OK
```

| Fichier | Tests | Portée |
|---|---|---|
| `tests/test_config.py` | 21 | Parsing, substitution, coercition booléenne, invariants de principes |
| `tests/test_doctor.py` | 18 | Scénarios de machine simulée, plafond d'isolation, robustesse du runner |
| `tests/test_obs_and_registry.py` | 22 | Rédaction de secrets, métriques, santé, honnêteté du registre |

Trois tests méritent d'être signalés :

- `test_redaction_happens_in_the_formatter` — vérifie qu'un secret passé par
  erreur dans un champ de log n'atteint pas la sortie. La rédaction ne dépend
  pas de la discipline de l'appelant.
- `test_runsc_absent_disables_red_team` — vérifie que le doctor énonce la
  *conséquence fonctionnelle* de l'isolation disponible, pas seulement le
  constat d'installation.
- `test_registry_cannot_overstate_reality` — empêche mécaniquement le registre
  de devenir optimiste. C'est la garantie derrière ce rapport.

**Non couvert :** aucun test d'intégration (rien à intégrer encore), aucun test
de charge, aucune mesure de couverture (`coverage` non installé dans
l'environnement de développement).

---

## 5. Limitations matérielles

| Limitation | Impact | Où c'est traité |
|---|---|---|
| GPU unique 24 Go | Un seul modèle lourd résident ; commutation de 15–40 s ; ~100–150 task-runs par nuit avec le MoE | Ordonnancement par affinité (phase 6), budget en minutes-GPU (`budget:` dans `config.yaml`) |
| Puissance statistique | Une nuit ne suffit pas à décider d'une promotion sur un effet faible | Tests à validité continue et expériences multi-nuits (phase 5 et 16) |
| WSL2 | Plafond d'isolation inférieur à Linux natif ; gVisor non garanti | ADR-0005, test go/no-go en phase 3, Red Team conditionné |
| Disque `.vhdx` | Croît sans se réduire seul | Partitionnement et rétention dès la phase 2 |
| Absence de C++ en vague 1 | Couverture linguistique réduite les premiers mois | Décision assumée (principe 11), métriques séparées prévues |

---

## 6. Risques connus à l'issue de la phase 1

| Risque | Gravité | Statut |
|---|---|---|
| Les vérifications du doctor ne correspondent pas aux sorties réelles des outils sur la machine cible | **Élevée** | Non mitigé — validation impossible ici. Première action de la phase 2 |
| Les tailles VRAM du catalogue de modèles sont estimées, non mesurées | Moyenne | Le check `gpu.config_match` détectera l'écart global, pas le détail par modèle |
| Le catalogue de modèles vieillit vite | Moyenne | C'est de la configuration, pas du code ; à revérifier à chaque phase majeure |
| Dérive entre le registre d'abstractions et la réalité | Faible | Mitigé mécaniquement (ADR-0007) |
| Secrets dans les journaux | Faible | Mitigé au niveau du formateur, testé |
| `docker-compose.yml` non exercé | Moyenne | Sera exercé dès la première commande de la phase 2 |

---

## 7. Prochaines dépendances (Phase 2 — Base de données)

Ce dont la phase 2 a besoin, et dans quel ordre :

1. **Validation terrain d'abord** — `apex doctor` sur la machine cible, correction
   des écarts. Bâtir la phase 2 sur des vérifications fausses coûterait plus cher
   que ce délai.
2. **Dépendances Python** : `sqlalchemy>=2.0`, `alembic`, `psycopg[binary]`,
   `pgvector`, `pydantic>=2.9` (extra `db`, déjà déclaré dans `pyproject.toml`).
3. **Rôles SQL** — `apex_core`, `apex_agent`, `apex_eval`, `apex_audit`,
   `apex_readonly`, créés par migration Alembic et non par le script d'init, afin
   d'être versionnés et testables. La séparation `apex_agent` / `apex_eval` est
   ce qui rendra l'anti-contamination réelle plutôt que déclarative.
4. **`audit_log` en INSERT seul** — révocation explicite de `UPDATE`, `DELETE`,
   `TRUNCATE` pour le rôle applicatif, avec test d'intégration qui *tente* la
   suppression et vérifie qu'elle échoue. Une politique non testée n'est pas une
   politique.
5. **Champ `trust_level` sur `compute_pools`** dès la première migration
   (principe 6), même s'il vaudra toujours `trusted` la première année.
6. **Partitionnement mensuel** sur `events`, `task_runs`, `model_runs`,
   `tool_usage`, `sandbox_execs` dès la création — rétrofitter un
   partitionnement sur des tables volumineuses est douloureux.
7. **Migrations testées à l'aller et au retour**, en CI, sur une base éphémère.

---

## 8. Porte qualité — verdict

| Critère | État |
|---|---|
| Tests unitaires | ✅ 61/61 |
| Tests d'intégration | ➖ sans objet en phase 1 |
| Gestion d'erreurs | ✅ taxonomie structurée, doctor ne plante jamais |
| Journalisation | ✅ JSON, contexte, rédaction des secrets |
| Contrôle de sécurité | ✅ invariants de configuration appliqués ; secrets hors du dépôt |
| Documentation | ✅ README, 8 ADR, ce rapport |
| Test de régression | ✅ 4 tests ajoutés sur le bug booléen |
| Validation sur matériel cible | ❌ **impossible ici** |

**Verdict : phase 1 fonctionnellement complète, mais non validée sur la machine
cible.** Le passage en phase 2 est conditionné à l'exécution de `apex doctor`
sur la machine réelle et à la correction des écarts constatés. Déclarer la
phase close sans cette étape reviendrait à faire précisément ce que §79
interdit.
