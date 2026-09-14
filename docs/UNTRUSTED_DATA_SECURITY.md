# APEX — Sécurité des données non fiables (architecture prévue, non implémentée)

**Rien de ce document n'est implémenté**, à l'exception des éléments explicitement
marqués REAL. Il fixe l'emplacement architectural de chaque étage du pipeline
avant que Web/Internet, l'ingestion de documents ou la multimodalité
n'existent réellement, dans le même esprit que `docs/EXTERNAL_INTELLIGENCE.md`
et `docs/COGNITIVE_ARCHITECTURE.md` : confronter chaque besoin aux
abstractions existantes avant d'en créer une nouvelle (ADR-0007).

---

## 1. La règle fondamentale

> Toute donnée provenant d'Internet, d'un téléchargement, d'une URL, d'un
> fichier reçu, d'une image, d'une vidéo ou d'une source inconnue est
> **UNTRUSTED par défaut**.

Ni le réseau (`SearchProvider`, PLANNED), ni un document ingéré, ni une image
ou une vidéo analysée ne doivent jamais devenir une source de vérité
automatique, ni recevoir de privilège au seul motif qu'APEX les a demandés.
APEX doit pouvoir **analyser** une donnée non fiable **sans lui donner de
privilèges** — c'est un principe distinct de la vérification épistémique
(`apex.memory.model.VerificationStatus`, étape 9) : une donnée peut être
`UNTRUSTED` (dangereuse à traiter) tout en devenant, après analyse, une
connaissance `VERIFIED` (vraie) — les deux questions ne se confondent jamais.

---

## 2. Le pipeline, confronté aux abstractions existantes

```
UNTRUSTED DATA
      ↓
QUARANTINE
      ↓
SECURITY ANALYSIS
      ↓
RISK ASSESSMENT
      ↓
SANDBOX si nécessaire
      ↓
EXTRACTION
      ↓
PROVENANCE
      ↓
MEMORY / KNOWLEDGE
```

| Étage | Composant | État réel |
|---|---|---|
| UNTRUSTED DATA (réception) | `ArtifactStore` (workspaces adressés par contenu — hash SHA-256, principe 5) | PLANNED |
| QUARANTINE | **Pas une nouvelle abstraction** : un artefact stocké dans `ArtifactStore` avec un statut de confiance emprunté à `apex.config.schema.TrustLevel` (`TRUSTED`/`UNTRUSTED`, déjà réel — voir §3) | Composition d'existant |
| SECURITY ANALYSIS / RISK ASSESSMENT | **`FileScanner`** (nouvelle entrée du registre, étape 9 — voir §4) | PLANNED, aucun code |
| SANDBOX | `SandboxProvider` (niveau NAMESPACED, STUB réel depuis l'étape 7) — un scan qui a besoin d'exécuter du contenu (ex. ouvrir un document, décompresser une archive) délègue à `SandboxProvider.execute()`, jamais un second chemin d'exécution | STUB réel |
| EXTRACTION | Sortie du `FileScanner`/d'un futur `ModelProvider` multimodal (`ModelRef.capability_class`, déjà une chaîne libre — aucune nouvelle abstraction pour la multimodalité elle-même) | PLANNED |
| PROVENANCE | `apex.memory.model.MemoryRecord` — `source`, `created_at`, `external_provenance`/`external_source` (déjà spécifiés, `docs/EXTERNAL_INTELLIGENCE.md` §3) | **REAL depuis l'étape 9** |
| MEMORY / KNOWLEDGE | `apex.memory.service.MemoryService` — `verification_status` reflète le résultat du cross-check, jamais promu `VERIFIED` par la seule confiance d'une source externe | **REAL depuis l'étape 9** |

**Verdict : une seule abstraction réellement manquante — `FileScanner`.** Tout
le reste du pipeline compose des briques déjà déclarées (`ArtifactStore`,
`TrustLevel`, `SandboxProvider`, `SecretBroker`, `MemoryProvider`), exactement
la même discipline anti-doublon déjà appliquée par
`docs/VISION_INTEGRATION.md` §2 et `docs/EXTERNAL_INTELLIGENCE.md` §2.

---

## 3. `TrustLevel` — déjà réel, pas une nouvelle notion de confiance

`apex.config.schema.TrustLevel` (`TRUSTED`/`UNTRUSTED`) existe déjà et
qualifie aujourd'hui un `ComputePool` (« seul un pool de confiance reçoit des
secrets ou le split HELDOUT »). Le pipeline ci-dessus **réutilise ce même
vocabulaire fermé**, appliqué à un artefact ingéré plutôt qu'à un pool de
calcul, plutôt que d'introduire un second concept de confiance parallèle
(« Quarantine » n'est donc jamais un statut supplémentaire à maintenir : un
artefact « en quarantaine » est simplement un artefact `TrustLevel.UNTRUSTED`
qui n'a pas encore franchi `FileScanner`).

---

## 4. `FileScanner` — le seul ajout réel (PLANNED)

Analyse un contenu non fiable (fichier, page web, image, vidéo) **avant**
toute extraction ou promotion en mémoire. N'exécute jamais lui-même de
contenu — un scan qui doit exécuter (ouvrir un document, décompresser une
archive, rendre une page) délègue à `SandboxProvider`, jamais un chemin
d'exécution parallèle qui contournerait la chaîne CORE → POLICY → SCHEDULER →
AGENT RUNTIME → SANDBOX déjà établie (étapes 7-8).

Sortie attendue (conception, non implémentée) : un verdict de risque + les
mêmes champs de traçabilité que `MemoryRecord` (hash SHA-256 du contenu
scanné, source, horodatage) — jamais un nouveau vocabulaire de statut : le
verdict alimente `VerificationStatus`/`confidence` d'un futur `MemoryRecord`,
il ne les remplace pas.

---

## 5. Ce qu'aucun contenu ni processus non fiable ne doit jamais recevoir

Repris tel quel de la consigne étape 9 — invariants de sécurité, pas des
suggestions :

- secrets, credentials, clés SSH, tokens (`SecretBroker`, PLANNED — mécanisme
  déjà prévu pour l'injection de secrets *hors* contexte modèle, jamais dans
  un contexte non fiable) ;
- accès direct à PostgreSQL (déjà refusé structurellement au niveau sandbox
  depuis l'étape 7 : `LinuxNamespaceSandbox` coupe le réseau, y compris la
  boucle locale vers l'hôte — vérifié empiriquement,
  `tests/test_sandbox_security.py::TestNetworkIsolation`) ;
- accès au filesystem de l'hôte (racine remontée en lecture seule + `/tmp`
  éphémère isolé, déjà réel depuis l'étape 7) ;
- accès aux sockets privilégiés, privilèges système inutiles (abaissement de
  privilège réel via `setpriv`, déjà réel depuis l'étape 7).

**Aucun de ces invariants n'est nouveau** : `SandboxProvider` (NAMESPACED) les
garantit déjà pour toute exécution qui y transite. Le travail des étapes
futures est de faire transiter TOUT traitement de contenu non fiable par ce
même mécanisme — jamais d'en créer un second, plus faible, « parce que ce
n'est qu'un scan ».

---

## 6. Browser Sandbox et contrôle réseau — extension de `SandboxProvider`, pas une nouvelle abstraction

Un futur rendu de page web dans un environnement isolé (« Browser Sandbox »)
et le contrôle réseau fin (proxy filtrant, domaines autorisés) sont des
**backends/capacités futurs de `SandboxProvider`**, comme `ContainerSandbox`
l'était avant la mission N+25 : `apex.sandbox.container.ContainerSandbox`
existe désormais réellement (Docker, `IsolationLevel.CONTAINERIZED`, réseau
coupé + utilisateur non-root + racine en lecture seule + cgroups, tous
vérifiés empiriquement — voir `apex.sandbox.container.
detect_container_capabilities`, jamais crus du seul fait que `docker` est
installé), sélectionné explicitement par `apex.sandbox.provider.
select_sandbox_provider()` quand NAMESPACED n'est pas disponible sur l'hôte.
Sur cette machine de développement, NAMESPACED reste disponible et Docker
injoignable : `ContainerSandbox` n'y est donc jamais exercé en intégration
réelle, ce qui est honnêtement documenté (jamais contourné) par
`tests/test_sandbox_container.py::TestRealDockerExecution`, sauté ici. Le
mode réseau `"proxy"` de `SandboxDefaults.network` reste, lui, non implémenté
sur LES DEUX backends (lève `NotImplementedYet` depuis l'étape 7). Aucune
nouvelle abstraction top-niveau n'a été créée pour ajouter ce second
backend : `apex.sandbox.provider.get_default_provider()` est resté le seul
point où le second backend a été branché — `AgentRuntime` et `MissionEngine`
n'ont rien eu à changer, exactement la garantie que cette section promettait
avant que `ContainerSandbox` n'existe.

---

## 7. Matériel — même discipline que le reste d'APEX

Aucune capacité de ce pipeline ne doit jamais être dérivée du matériel de la
machine de développement actuelle. Un futur `FileScanner`/Browser Sandbox
plus gourmand en ressources que ce que cette machine peut fournir reste une
capacité **architecturale** valide, à déclarer via configuration explicite
(même mécanisme que `apex.config.schema.PoolStatus.TARGET`/
`HardwareThresholds`, déjà en place depuis la correction architecturale de
l'étape 7) — jamais un seuil codé en dur dans `apex/sandbox/` ou `apex/memory/`.

---

## 8. Ordre d'introduction (indicatif, à confirmer à l'implémentation)

| Dépend de | Élément | Statut |
|---|---|---|
| — | `MemoryProvider` (provenance, `VerificationStatus`, relations SUPPORTS/CONTRADICTS) | **REAL, étape 9** |
| `MemoryProvider` | `ArtifactStore` (stockage adressé par contenu, hash SHA-256) | PLANNED |
| `ArtifactStore` | `TrustLevel` appliqué à un artefact ingéré (§3) | PLANNED — pas de nouveau code, réutilisation |
| `ArtifactStore`, `SandboxProvider` | `FileScanner` (§4) | PLANNED |
| `FileScanner` | `SearchProvider` (Web/Internet) — ne peut écrire en mémoire qu'après `FileScanner` | PLANNED |
| `SandboxProvider` | Browser Sandbox / réseau `"proxy"` (§6) | PLANNED |

Ce document ne fixe pas de numéro d'étape définitif — seulement l'ordre de
dépendance réelle, à confronter à la prochaine validation humaine avant tout
code.
