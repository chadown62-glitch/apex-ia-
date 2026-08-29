# APEX — Rapport de fin de Phase 2

**Phase :** 2 — Base de données
**Version :** 0.1.2 (baseline) + persistance PostgreSQL
**Tests :** 205 exécutés, 204 réussis, 0 échec, 1 saut explicite documenté
**Volume :** ~1 280 lignes ajoutées (`apex/db/`, `migrations/`, `tests/test_db_*`)

Ce rapport suit le même principe que `docs/PHASE_1_REPORT.md` (verrouillé
n°15) : ce qui est réellement implémenté et testé, ce qui n'a pu être que
simulé ou sauté, ce qui reste hors périmètre. `docs/PHASE_1_REPORT.md`
n'a pas été modifié — c'est un rapport historique de la v0.1.0 et le reste.

---

## 1. Réellement implémenté et testé

| Composant | Détail | Tests |
|---|---|---|
| `apex.db.models` | `ComputePoolRow`, `EventRow`, `AuditLogRow` — schéma déclaratif SQLAlchemy 2.0 | couvert indirectement |
| `apex.db.engine` | Construction d'URL (bootstrap + par rôle), session scope transactionnelle | 3 |
| `apex.db.repositories` | `ComputePoolRepository`, `EventRepository`, `AuditLogRepository` (INSERT seul, aucune méthode de modification exposée) | 5 |
| `apex.db.event_sink` | `DatabaseEventSink` — registre : `EventSink` STUB → IMPLEMENTED | 6 |
| `migrations/versions/…initial_schema.py` | Rôles applicatifs, tables, partitionnement natif, GRANT/REVOKE | voir §2 |
| ADR-0010 | Décisions de cette phase, y compris ce qui a été délibérément écarté | — |

### Invariants réellement appliqués par le code

- `apex.db.event_sink` reste importable sans l'extra `db` installé (import
  SQLAlchemy différé, même pattern que `apex.config.loader.load_config`
  pour PyYAML) — sinon `apex doctor` casserait sur `interfaces.integrity`
  pour une raison sans rapport avec le code. Testé en cachant `sqlalchemy`
  de `sys.modules` et en import à froid.
- `AuditLogRepository` n'expose aucune méthode `update`/`delete` — défense
  en profondeur en plus de la révocation SQL.
- `DatabaseEventSink.emit()` réapplique `apex.obs.logging.redact()` avant
  d'écrire en base : un secret passé par erreur en contexte est masqué,
  testé explicitement (`test_emit_redacts_secrets_before_persisting`).
- Aucun mot de passe de rôle en dur : la migration lève si une variable
  `APEX_DB_<RÔLE>_PASSWORD` manque.

### Bug trouvé et corrigé pendant la phase

Flipper `EventSink` à `IMPLEMENTED` avec `module="apex.db.event_sink"`
cassait immédiatement `test_registry_cannot_overstate_reality` et le check
`interfaces.integrity` du doctor sur tout environnement sans l'extra `db`
installé (`import sqlalchemy` en tête de module levait `ImportError`,
capturée par `module_importable()` comme "non implémenté"). Corrigé en
différant l'import de SQLAlchemy à l'intérieur des méthodes. Un test de
non-régression existe (`TestEventSinkImportableWithoutSqlalchemy`).

Deux bugs réels dans la migration, trouvés uniquement parce que testés
contre PostgreSQL réel (SQLite ne les aurait jamais révélés) :
`GRANT USAGE` manquant sur les séquences d'auto-incrément (`INSERT`
échouait avec « permission denied for sequence ») et un ordre de nettoyage
de test incorrect (`DROP ROLE` avant `DROP DATABASE`).

---

## 2. Validé contre PostgreSQL réel — pas seulement simulé

**C'est la section la plus importante de ce rapport**, comme pour la phase 1.
Contrairement à la phase 1, cette phase a pu être validée contre une
instance PostgreSQL 16 **réelle**, pas seulement contre `FakeProbe` ou
SQLite : le paquet `postgresql-16` était déjà installé dans ce conteneur
Claude Code (mais arrêté) ; le service a été démarré localement avec
autorisation explicite de l'utilisateur avant toute écriture de test de
sécurité — aucune image Docker tirée, aucun paquet supplémentaire installé,
action entièrement réversible.

| Élément | Validé ici, réellement | Ce qui ne l'a pas été |
|---|---|---|
| Migration `upgrade`/`downgrade`/aller-retour | Oui — contre PostgreSQL 16 | Contre l'image `pgvector/pgvector:pg16` de `docker-compose.yml` (différente du paquet système utilisé ici) |
| 5 rôles créés, `LOGIN`, mots de passe par variable | Oui | — |
| `audit_log` : `apex_audit` INSERT seul, `UPDATE`/`DELETE`/`TRUNCATE` refusés | Oui, connexion réelle avec le rôle (pas le superutilisateur) | — |
| `apex_core` : lecture seule sur `audit_log` | Oui | — |
| `apex_agent` : peut journaliser des événements, ne peut pas modifier `compute_pools` | Oui | — |
| `apex_readonly` : lecture seule partout | Oui | — |
| Partitionnement natif mensuel de `events` (+ partition `DEFAULT`) | Oui — partitions créées et vérifiées via `\d+ events` | Comportement sous charge réelle, maintenance des partitions futures (opérationnel, hors périmètre) |
| pgvector | **Non** | Extension absente du paquet PostgreSQL système de ce conteneur ; hors périmètre de toute façon (aucune colonne d'embedding en phase 2) |
| Séparation HELDOUT (critère §5.3 du brief) | **Non, délibérément reporté** | Exige une colonne `split` appartenant au schéma de `TaskBank` (`PLANNED`, phase 5) — voir ADR-0010. Test présent, `@unittest.skip` avec motif, jamais simulé comme passant |

**Première action à vérifier en phase 3+ ou lors d'un déploiement WSL2** :
rejouer `alembic upgrade head` contre le PostgreSQL de `docker-compose.yml`
(image `pgvector/pgvector:pg16`) pour confirmer que le comportement observé
ici (paquet système Ubuntu) est identique à celui de l'image conteneurisée
réellement déployée.

---

## 3. Stubs, abstractions et périmètre délibérément non couvert

`EventSink` : `STUB` → `IMPLEMENTED`. Aucune autre abstraction du registre
n'a changé de statut. `CheckpointStore`, `PolicyEngine`, `SecretBroker`
restent `PLANNED`, conformément au brief (`docs/VISION_INTEGRATION.md` §5 :
« schéma seulement » pour `PolicyEngine`/`SecretBroker`, aucun schéma ajouté
ici pour eux non plus — il n'existait pas de schéma à ajouter qui ne soit
pas déjà anticipation).

**Tables volontairement absentes.** `task_runs`, `model_runs`, `tool_usage`,
`sandbox_execs` — nommées par `docs/PHASE_1_REPORT.md` §6 pour le
partitionnement day-one — n'ont pas été créées : leurs abstractions
(`TaskBank`, `ModelProvider`, `ToolProvider`/`ToolRegistry`,
`SandboxProvider`) restent `PLANNED` (phases 3 à 7). Voir ADR-0010 pour le
raisonnement complet ; c'est un risque documenté, pas un oubli.

---

## 4. Tests

```
Ran 205 tests — 204 OK, 1 skip explicite (PostgreSQL disponible)
Ran 205 tests — 204 OK, 15 skip explicites (PostgreSQL absent, ex. venv sans extra db)
```

| Fichier | Tests | Portée |
|---|---|---|
| `tests/test_db_models.py` | 15 | Modèles/repositories/EventSink via SQLite en mémoire ; garde de non-régression sur l'import différé de SQLAlchemy |
| `tests/test_db_migrations.py` | 4 | Migration réelle contre PostgreSQL : tables, partitions, rôles, aller-retour — `SKIP` explicite si PostgreSQL indisponible |
| `tests/test_db_security.py` | 11 (10 réels + 1 skip HELDOUT) | GRANT/REVOKE réels, connexion avec le rôle applicatif (jamais le superutilisateur) |

Aucun test des 189 précédents (baseline + audit) n'a été modifié ni
supprimé. Aucun FAIL masqué : les deux bugs de migration trouvés ont été
corrigés à la racine, pas contournés.

---

## 5. Risques connus à l'issue de la phase 2

| Risque | Gravité | Statut |
|---|---|---|
| PostgreSQL de `docker-compose.yml` (image `pgvector/pgvector:pg16`) non encore exercé — seul le paquet système de ce conteneur l'a été | Moyenne | À valider en phase 3 ou au premier déploiement WSL2 |
| Partitionnement des 4 tables restantes reporté aux phases qui implémentent leurs abstractions | Moyenne | Documenté (ADR-0010), pas silencieux — retrofit reste douloureux si oublié |
| Critère §5.3 (HELDOUT) non validé | Faible-Moyenne | Reporté explicitement à la phase 5 (TaskBank), test stub présent |
| Maintenance des partitions futures de `events` (au-delà des 3 mois créés par la migration) | Faible | Travail opérationnel non couvert par cette migration — partition `DEFAULT` évite un échec d'INSERT en attendant |
| `patch_k1_gate11.py` | Faible | Inchangé cette phase, conformément à l'instruction explicite de ne pas le supprimer |

---

## 6. Porte qualité — verdict

| Critère | État |
|---|---|
| Tests unitaires (SQLite) | ✅ 15/15 |
| Tests d'intégration (PostgreSQL réel) | ✅ 14/14 (+ 1 skip documenté) |
| Migration aller-retour | ✅ validée contre PostgreSQL réel |
| Sécurité SQL (GRANT/REVOKE, INSERT-only) | ✅ validée contre PostgreSQL réel, connexion par rôle |
| Registre d'abstractions honnête | ✅ `EventSink` implemented, importable avec et sans l'extra `db` |
| Documentation | ✅ ADR-0010, ce rapport |
| Régression | ✅ 189 tests de la baseline inchangés, tous verts |
| Déploiement `docker-compose.yml` réel | ❌ non exercé dans ce conteneur (voir §2) |
| pgvector | ❌ hors périmètre (voir §2) |
| Séparation HELDOUT | ❌ reportée explicitement (voir §2, ADR-0010) |

**Verdict : les objectifs du brief phase 2 explicitement dans le périmètre
(`ComputePool`, `EventSink`, `audit_log` INSERT-only) sont implémentés et
validés contre PostgreSQL réel — pas seulement simulés.** Deux points
restent à confirmer sur le déploiement cible réel (`docker-compose.yml` sur
WSL2) avant de les considérer définitivement clos : le comportement de
l'image `pgvector/pgvector:pg16` elle-même, et pgvector si une phase
ultérieure en a besoin plus tôt que prévu.
