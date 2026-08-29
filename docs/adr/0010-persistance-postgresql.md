# ADR-0010 — Persistance PostgreSQL : SQLAlchemy 2.0, Alembic, rôles créés par migration

**Statut :** Acceptée — phase 2

## Contexte

`scripts/init-db/01-extensions.sql` (phase 1) annonçait déjà ce report : les
rôles applicatifs, les tables et la contrainte INSERT-only sur `audit_log`
devaient arriver par Alembic, pas par le script d'init, pour rester
versionnés et testables. Cette ADR documente les décisions prises en les
implémentant.

## Décisions

**SQLAlchemy 2.0 (style déclaratif `Mapped`/`mapped_column`) + Alembic.**
Extra `pyproject.toml` déjà déclaré en phase 1, aucun changement de version.

**Les rôles sont créés par la migration initiale, pas par
`scripts/init-db/01-extensions.sql`.** Cinq rôles : `apex_core`,
`apex_agent`, `apex_eval`, `apex_audit`, `apex_readonly`, chacun avec un mot
de passe lu depuis `APEX_DB_<RÔLE>_PASSWORD` au moment où la migration
s'applique — jamais une valeur par défaut (§72). La migration échoue si une
variable manque.

**`events` est la seule table partitionnée nativement en phase 2.**
`docs/PHASE_1_REPORT.md` §6 nommait cinq tables à forte cardinalité
(`events`, `task_runs`, `model_runs`, `tool_usage`, `sandbox_execs`) pour un
partitionnement mensuel dès la création. Seule `events` appartient à une
abstraction déjà implémentée (`EventSink`) ; les quatre autres appartiennent
à des abstractions encore `PLANNED` (`TaskBank` phase 5, `ModelProvider`
phase 6, `ToolProvider`/`ToolRegistry` phase 7, `SandboxProvider` phase 3).
Créer leur schéma maintenant reviendrait à deviner une implémentation qui
n'existe pas — exactement ce qu'interdit ADR-0007. Le partitionnement de ces
quatre tables reste donc un risque documenté, porté par la phase qui
introduit chacune d'elles, pas différé par oubli.

**Contrainte technique du partitionnement natif.** PostgreSQL exige que
toute contrainte `UNIQUE`/`PRIMARY KEY` d'une table partitionnée inclue la
colonne de partitionnement. Plutôt que d'imposer une clé composite `(id,
ts)` jusque dans le modèle SQLAlchemy — ce qui aurait cassé l'auto-incrément
portable utilisé par les tests génériques sous SQLite — `events.id` est
alimenté par une séquence dédiée (unicité garantie en pratique) et la seule
contrainte SQL est `UNIQUE(id, ts)`. Compromis documenté dans
`migrations/versions/a6227d98f7d7_initial_schema.py`, pas un oubli.

**Import de SQLAlchemy différé dans `apex/db/event_sink.py`.** Le module
enregistré au registre (`apex.interfaces.registry`) doit être importable
même quand l'extra `db` n'est pas installé — sinon `apex doctor` échouerait
sur `interfaces.integrity` pour une raison sans rapport avec une régression
du code. Même pattern que `apex.config.loader.load_config` pour PyYAML
(ADR-0002) : import différé à l'intérieur des méthodes, jamais au niveau du
module.

**Critère de validation §5.3 du brief reporté, pas simulé.** « Le rôle
worker ne peut pas lire le split HELDOUT » exige une colonne `split` sur une
table de tâches — un fragment du schéma de `TaskBank` (`PLANNED`, phase 5).
Un test existe, explicitement `@unittest.skip` avec le motif, plutôt qu'une
table fictive construite pour la seule occasion de faire passer ce test.

## Conséquence

`EventSink` passe de `STUB` à `IMPLEMENTED` au registre. Le sink JSON
existant (`apex.obs.logging`) n'est pas remplacé — le sink DB vient en plus,
conformément au brief.

## Validation

Rôles, `GRANT`/`REVOKE`, aller-retour de migration et partitionnement natif
ont été validés contre une instance PostgreSQL 16 réelle (pas seulement
SQLite) dans l'environnement Claude Code de cette phase — le paquet
`postgresql-16` y était déjà installé mais arrêté ; le service a été démarré
localement avec autorisation explicite, aucune image Docker tirée, aucun
paquet supplémentaire installé. Deux bugs réels ont été trouvés et corrigés
par cette validation, tous deux indétectables via SQLite seul : un `GRANT
USAGE` manquant sur les séquences (`INSERT` échouait avec « permission
denied for sequence »), et un ordre de nettoyage de test incorrect (`DROP
ROLE` avant `DROP DATABASE` échoue tant que les `GRANT` existent encore).

Reste à valider sur la machine cible (WSL2, `docker-compose.yml`) : le
déploiement conteneurisé lui-même (l'image `pgvector/pgvector:pg16`, distincte
du paquet système utilisé ici) et l'extension `pgvector`, absente du paquet
PostgreSQL système de ce conteneur et hors périmètre de cette phase (aucune
colonne d'embedding n'existe encore).

## Écarté

Créer les quatre tables `task_runs`/`model_runs`/`tool_usage`/`sandbox_execs`
dès maintenant pour anticiper le partitionnement futur : rejeté, voir
ci-dessus (ADR-0007). Une clé composite `(id, ts)` uniforme sur `events` côté
SQLAlchemy : rejetée, casse l'auto-incrément portable des tests génériques
pour un gain de rigueur marginal (`UNIQUE(id, ts)` suffit).
