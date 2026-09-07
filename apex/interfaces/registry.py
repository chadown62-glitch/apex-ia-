"""Registre des abstractions APEX.

Ce module est la réponse structurelle aux principes verrouillés 12 et 15 : il
est la source unique de vérité sur **ce qui existe réellement** par opposition
à ce qui est prévu. Il est lu par `apex doctor`, par le rapport de fin de phase
et, plus tard, par le Dashboard.

Un état n'est pas une intention : il est vérifié par un test de contrat
(`tests/test_interfaces_registry.py`) qui échoue si une abstraction déclarée
IMPLEMENTED n'a pas d'implémentation importable, ou si une abstraction déclarée
PLANNED en possède une. Le registre ne peut donc pas mentir sur la durée.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum


class ImplStatus(str, Enum):
    #: Interface définie ET implémentation réelle utilisable dès maintenant.
    IMPLEMENTED = "implemented"
    #: Interface définie ; implémentation triviale ou partielle explicitement signalée.
    STUB = "stub"
    #: Interface définie, aucune implémentation ; lève NotImplementedYet.
    DEFINED = "defined"
    #: Rien encore, phase de définition annoncée.
    PLANNED = "planned"


@dataclass(frozen=True)
class Abstraction:
    name: str
    purpose: str
    phase_defined: int
    phase_implemented: int
    status: ImplStatus
    fallback: str
    module: str | None = None
    notes: str = ""

    @property
    def is_available(self) -> bool:
        return self.status in (ImplStatus.IMPLEMENTED, ImplStatus.STUB)


#: Les 23 abstractions arrêtées en Phase 0 (addendum, §6).
#: `status` reflète l'état RÉEL à la fin de la phase 1 — il sera mis à jour
#: phase par phase, et jamais par anticipation.
ABSTRACTIONS: tuple[Abstraction, ...] = (
    Abstraction(
        "ModelProvider", "génération, streaming, comptage de tokens", 4, 6,
        ImplStatus.STUB, "aucun — refuse la génération si le backend n'est pas vérifié disponible",
        module="apex.models.provider",
        notes="STUB et non IMPLEMENTED délibérément (Phase 6, partiel) : "
              "`OllamaProvider` est réel — `detect()` sonde `GET /api/tags` "
              "(HTTP réel, jamais un simple port ouvert), `generate()` "
              "appelle `POST /api/generate` réellement, timeout et "
              "annulation (`threading.Event`, coopératif) réels. "
              "`CapabilityStatus.UNAVAILABLE_ON_DEV` sur cette machine "
              "(Ollama injoignable, vérifié empiriquement), jamais "
              "`UNSUPPORTED`. `generate_stream()` (streaming réel par "
              "lecture de blocs) est une méthode CONCRÈTE de "
              "`OllamaProvider`, délibérément hors du contrat abstrait "
              "`ModelProvider` — à généraliser plus tard seulement si "
              "plusieurs backends en ont réellement besoin. Aucun modèle "
              "codé en dur : l'appelant fournit toujours un `ModelEndpoint` "
              "déjà résolu (`apex.config.schema`, existant). Hors périmètre "
              "de cette étape, toujours PLANNED : `Placement` (résolution "
              "rôle -> endpoint), `EmbeddingProvider`, comptage de tokens/s "
              "(Model Capability Gate). Aucun raccordement à "
              "`MissionEngine`/`AgentRuntime`, ni à `TaskBank`/`EvalRunner`/"
              "`OracleProvider` (devenus STUB en Phase 5, mais "
              "`task.command` y reste une commande déterministe déjà "
              "stockée — jamais générée par ce `ModelProvider`, ni par "
              "aucun autre). Un appel réel contre un vrai Ollama n'a pas été "
              "prouvé sur DEV_MACHINE (Ollama absent ici) — seul le "
              "comportement structurel et le refus honnête sans backend "
              "sont vérifiés ; un test d'intégration réel existe, "
              "explicitement sauté si Ollama n'est pas joignable, jamais "
              "simulé comme passant. **MissionEngine<->ModelProvider "
              "désormais RACCORDÉ, HORS sandbox** (mis à jour après la "
              "passe « première IA utilisable ») : "
              "`apex.missions.execution.generate_mission_response` est un "
              "second chemin d'exécution de Mission (à côté de "
              "`launch_mission`), appelé au niveau ORCHESTRATEUR — jamais "
              "depuis un clone sandboxé, donc AUCUN contournement de la "
              "sandbox. Câblage prouvé par test contre un double de test "
              "fidèle au contrat `ModelProvider` (jamais présenté comme une "
              "preuve d'inférence réelle) ; un test d'intégration RÉEL "
              "existe (`tests/test_missions_model_generation.py::"
              "TestRealOllamaMissionIntegration`), auto-exécuté dès qu'un "
              "Ollama réel est joignable (local ou via `APEX_OLLAMA_URL`), "
              "explicitement sauté sinon. `MissionEngine`/`AgentRuntime` -> "
              "sandbox -> `ModelProvider` (un clone sandboxé appelant un "
              "modèle réseau) reste bloqué pour la raison déjà établie : "
              "`apex.sandbox.linux_namespace` lève `NotImplementedYet` pour "
              "tout `network` autre que `\"none\"` — mode `\"proxy\"` "
              "encore PLANNED, un changement de surface de sécurité réel, "
              "délibérément hors périmètre de cette passe. **Second backend "
              "réel : `OpenAICompatibleProvider`** (passe « Multi-Model/"
              "Provider Fabric ») — UN SEUL adapter HTTP générique pour "
              "tout service exposant `/v1/chat/completions`/`/v1/models` "
              "(OpenAI, OpenRouter, Together, Groq, Fireworks, DeepSeek, "
              "Mistral, xAI, Qwen en mode compatible), jamais une classe "
              "par fournisseur. `get_default_provider()` dispatche "
              "désormais réellement sur `ModelEndpoint.runtime` "
              "(`\"ollama\"` -> `OllamaProvider`, tout le reste -> "
              "`OpenAICompatibleProvider`) — le point de choix unique déjà "
              "prévu par sa propre docstring, pas une bifurcation "
              "nouvelle. Authentification via `SecretBroker` "
              "(`ModelEndpoint.secret_key`, nouveau champ additif — jamais "
              "une clé en dur, jamais journalisée). **Aucun appel réel "
              "contre un vrai service payant n'a été prouvé** — testé "
              "contre un serveur HTTP de test imitant la forme exacte des "
              "réponses OpenAI-compatible (`tests/"
              "test_models_openai_compatible.py`), plus un test "
              "d'intégration réel explicitement sauté sans "
              "`APEX_OPENAI_COMPATIBLE_URL`/clé fournie — jamais simulé "
              "comme un provider externe opérationnel.",
    ),
    Abstraction(
        "EmbeddingProvider", "vectorisation pour la mémoire", 4, 8,
        ImplStatus.PLANNED, "bge-m3 local",
    ),
    Abstraction(
        "ModelRouter", "sélection d'un ModelProvider selon capacité/coût/latence/préférence", 6, 6,
        ImplStatus.STUB, "un seul candidat local (Ollama), sélection triviale",
        module="apex.routing.router",
        notes="STUB et non IMPLEMENTED délibérément — nouvelle abstraction "
              "explicitement autorisée (directive « Multi-LLM/Provider "
              "Fabric »), passée au test en 4 points avant création : aucun "
              "composant existant ne porte la responsabilité de comparer "
              "plusieurs `ModelCandidate` et d'en choisir un (`Placement`, "
              "PLANNED phase 6, couvre l'assignation run -> pool de calcul, "
              "un axe différent) ; l'ajouter à `ModelProvider` violerait son "
              "contrat (un provider ne sait pas comparer d'autres "
              "providers) ; ≥2 consommateurs réels dès cette passe "
              "(`generate_mission_response_via_router`, et tout futur appel "
              "multi-candidats) ; aucun doublon (`docs/EXTERNAL_INTELLIGENCE"
              ".md`/`docs/VISION_INTEGRATION.md` concluaient qu'aucune "
              "abstraction séparée n'était nécessaire AU NIVEAU CONCEPTUEL, "
              "mais aucun des deux documents ne fournissait de logique de "
              "sélection réelle — celle-ci n'existait nulle part avant "
              "cette passe). `select_model()` (`apex.routing.router`) est "
              "une fonction pure et déterministe : filtre par capacité "
              "requise (`apex.routing.model.Capability`, vocabulaire fermé, "
              "dérivée UNIQUEMENT de signaux réels — `ModelRef."
              "capability_class`, localité, `max_context` ; `REASONING`/"
              "`VISION`/`TOOL_USE` restent dans le vocabulaire mais ne sont "
              "JAMAIS dérivées faute de signal réel dans le schéma actuel), "
              "contexte minimal, disponibilité réelle (`ModelCandidate."
              "is_available`, jamais une intention `ModelRef` confondue "
              "avec un `ModelEndpoint` réel), et `ExternalPolicy` (jamais "
              "contournée). Score TRANSPARENT (base 1.0 + 0.5 préférence "
              "locale), PAS un `historical_quality`/`cost_score`/"
              "`latency_score` inventé — ces axes restent explicitement "
              "`PLANNED`, faute de source de données réelle aujourd'hui : "
              "le point d'entrée existe déjà (`EvalRun.facts[\"strategy\"]`, "
              "`apex.selfmodel`, `apex.costgain.CostGainEstimator`) sans "
              "être câblé, plutôt que simulé. `generate_with_fallback`/"
              "`generate_all_candidates` (`apex.routing.generation`) "
              "composent `ModelProvider.generate()` sur plusieurs candidats "
              "classés — le second n'exécute AUCUNE sélection de « meilleure "
              "réponse » (lacune réelle, documentée : comparer du texte "
              "libre exigerait un jugement que `Tier0Judge` — contrat "
              "`judge(sandbox_result, task)` — ne couvre pas). Aucun secret "
              "en dur : tout provider payant futur passera par "
              "`SecretBroker`, jamais une clé hors de ce point unique.\n\n"
              "Passe « Model/Provider Catalog » (`apex.models.catalog`) : "
              "PAS une nouvelle abstraction — extension de la couche de "
              "PRÉPARATION en amont du Router, retestée en 4 points : (1) "
              "aucun composant existant ne porte déjà une vue structurée "
              "provider+modèle avec disponibilité/découverte/capacités — "
              "`apex.models.registry.ModelCandidate` reste une résolution "
              "brute, `select_model` un choix, ni l'un ni l'autre une vue "
              "consultable ; (2) l'ajouter à `select_model()` violerait son "
              "contrat (une fonction de décision, pas un catalogue "
              "parcourable/filtrable indépendamment d'une requête) ; (3) "
              "≥2 consommateurs réels : `usable_candidates()` alimente "
              "`select_model` sans rien dupliquer, et toute future API/CLI "
              "de listing de modèles/providers (aucune n'existe encore) ; "
              "(4) aucun doublon — `CatalogModel`/`ProviderInfo` sont des "
              "vues DÉRIVÉES de `ModelCandidate`/`ModelEndpoint` déjà réels, "
              "jamais stockées, recalculées à chaque appel. `Availability` "
              "(AVAILABLE/UNAVAILABLE/UNKNOWN/DISABLED) et `DiscoverySource` "
              "(CONFIGURED/DISCOVERED/VERIFIED) sont deux vocabulaires "
              "fermés distincts et honnêtes : `UNKNOWN` n'est JAMAIS promu "
              "à `AVAILABLE` sans un `detect()` réel via `discover_live()` "
              "(seul point du module qui touche le réseau, jamais appelé "
              "implicitement) ; `VERIFIED` reste explicitement PLANNED — "
              "aucune Mission ne persiste aujourd'hui QUEL `ModelEndpoint` "
              "l'a réellement servie (`Mission.result` ne porte que le "
              "texte), donc rien à interroger pour prouver un usage réel. "
              "`Capability` (`apex.routing.model`) étendu de `TEXT`/`AUDIO`/"
              "`VOICE`/`VIDEO`/`SCREEN` (vocabulaire fermé, prêt pour de "
              "futurs `ModelEndpoint` multimodaux réels) — seule `TEXT` est "
              "dérivée aujourd'hui (chaque backend réel est une API de "
              "génération de texte, une affirmation vérifiée par "
              "construction) ; les 4 autres ne sont JAMAIS dérivées, faute "
              "de tout signal réel dans le schéma. `PerformanceHistory` "
              "toujours vide par construction (`usage_count=0`) — aucune "
              "statistique inventée, un emplacement prêt, pas une mesure.\n\n"
              "Passe « Adaptive Model Router V1 » — le point d'entrée "
              "resté vide ci-dessus est maintenant câblé, jamais simulé : "
              "IMPLEMENTED dans cette passe : (1) persistance réelle de "
              "`GenerationResult.elapsed_s`/`tokens_prompt`/"
              "`tokens_completion` (jusqu'ici mesurés puis jetés) sur "
              "`agent_executions` (`elapsed_s`/`tokens_prompt`/"
              "`tokens_completion`, colonnes nullable, migration "
              "`c3e5f7a9b1d2`) ; (2) `apex.agent_registry.performance."
              "aggregate_model_performance` — fonction PURE construisant "
              "`apex.routing.model.ModelPerformanceStats` (usage/succès/"
              "échecs/taux de succès/latence moyenne/tokens moyens/motifs "
              "d'échec) depuis des `AgentExecution` réelles, jamais une "
              "DB elle-même ; (3) `select_model(..., performance=, "
              "scoring_policy=)` — deux paramètres STRICTEMENT optionnels "
              "(compatibilité stricte prouvée par test : tout appel "
              "existant qui les omet produit exactement la même "
              "`RoutingDecision` qu'avant) ; le score devient un "
              "`apex.routing.model.ScoreBreakdown` — contributions NOMMÉES "
              "(`capability_match`/`local_preference`/"
              "`historical_reliability`/`historical_latency`/"
              "`eval_quality`/`cost`), jamais un chain-of-thought ; (4) "
              "`apex.config.schema.RouterScoringPolicy` (`routing:` dans "
              "`config.yaml`, même patron que `SchedulerPolicy`) — poids "
              "explicites, validés, configurables ; les défauts "
              "(`capability_match_weight=1.0`, `local_preference_weight="
              "0.5`) reproduisent EXACTEMENT le score déjà en production "
              "avant cette passe, testé explicitement.\n\n"
              "DORMANT / NOT WIRED — passe « Hardening », à lire avant toute "
              "conclusion sur ce que fait le Router AUJOURD'HUI EN "
              "PRODUCTION : aucun des trois points d'appel réels de "
              "`select_model` ne fournit `performance=` (vérifié par lecture "
              "directe de chaque site d'appel, pas une supposition) — ni "
              "`apex.agent_registry.service.AgentRegistryService."
              "run_execution`, ni `apex.missions.execution."
              "generate_mission_response_via_router`, ni `apex.conversations."
              "service` (ConversationService). Le mécanisme adaptatif décrit "
              "ci-dessus est donc entièrement IMPLEMENTED au sens « code "
              "testé, correct, prêt » mais ENTIÈREMENT DORMANT au sens "
              "comportemental : zéro Mission/Conversation réelle n'est "
              "aujourd'hui routée différemment qu'avant cette passe. Le "
              "Router N'APPREND PAS des performances des modèles en "
              "production — il POURRA le faire le jour où un futur passage "
              "câble explicitement `list_by_model` -> "
              "`aggregate_model_performance` -> `select_model(performance=)` "
              "dans ces trois sites d'appel, ce qui n'est ni fait ni prévu "
              "par cette passe. Avant ce câblage, voir le risque de "
              "starvation par sélection gloutonne documenté dans le rapport "
              "« Adaptive Model Router V1 — Hardening » (§ EXPLORATION/"
              "EXPLOITATION DESIGN) — un mécanisme d'exploration explicite y "
              "est requis, PLANNED, jamais improvisé au moment du câblage.\n\n"
              "PLANNED, toujours honnêtement non simulé : `eval_quality` "
              "(`ScoreContribution` toujours `UNKNOWN` — `EvalRunner` "
              "n'invoque aucun `ModelProvider`, `EvalRunRow` n'a pas de "
              "colonne `model_id`, aucun lien model↔verdict n'existe ; "
              "rendre mesurable exigerait de relier `EvalRunner` à un "
              "`ModelProvider` pour des tâches à réponse libre, hors "
              "périmètre de cette passe) ; `cost` (`ScoreContribution` "
              "toujours `UNKNOWN` — aucun champ prix dans `ModelEndpoint`/"
              "`ModelRef` ; rendre mesurable exigerait un champ "
              "explicitement DÉCLARÉ, jamais confondu avec une mesure) ; "
              "mapping `Mission.specialty` -> `Capability` (`Specialty."
              "capabilities` reste du texte libre humain, aucune "
              "correspondance vers le vocabulaire fermé `Capability` "
              "n'existe dans le code — vérifié, pas fabriqué ici) ; "
              "`difficulty` (aucun champ de ce nom n'existe sur `Task`/"
              "`Mission`, vérifié) ; activation du feedback historique EN "
              "PRODUCTION (câblage explicite de `performance=` dans les "
              "trois sites d'appel réels ci-dessus — voir DORMANT / NOT "
              "WIRED) ; une stratégie d'exploration/exploitation "
              "(epsilon-greedy, UCB ou Thompson sampling sur `success_rate`) "
              "AVANT ce câblage — sans elle, une sélection gloutonne top-1 "
              "peut durablement favoriser le premier candidat ayant "
              "accumulé un historique, au détriment d'alternatives jamais "
              "réessayées (voir le rapport « Hardening », design documenté, "
              "aucune valeur arbitraire introduite ici).",
    ),
    Abstraction(
        "ArtifactStore", "workspaces adressés par contenu", 3, 3,
        ImplStatus.STUB, "MinIO local",
        module="apex.artifacts.provider",
        notes="STUB et non IMPLEMENTED délibérément (étape 10) : principe 5 "
              "(identité par hash SHA-256, format canonique `sha256:<64 hex>`, "
              "jamais par chemin) réel et testé pour "
              "`LocalFilesystemArtifactStore` — création paresseuse du "
              "répertoire, écriture atomique (fichier temporaire + "
              "`os.replace`), vérification d'intégrité systématique à la "
              "lecture, chemin filesystem TOUJOURS dérivé d'un hash déjà "
              "validé (path traversal structurellement impossible, pas "
              "seulement filtré). Aucun quota — limitation assumée et "
              "documentée, pas un oubli. `MinioArtifactStore` est "
              "architecturé (même contrat `ArtifactStoreProvider`, "
              "`detect()` sonde une vraie connexion TCP) mais ses "
              "opérations de stockage lèvent `NotImplementedYet` : MinIO "
              "n'est pas joignable sur cette machine de développement "
              "(`CapabilityStatus.UNAVAILABLE_ON_DEV`, jamais "
              "`UNSUPPORTED` — voir `apex.obs.capability`), donc jamais "
              "prétendu validé. Aucun couplage avec SandboxProvider/"
              "Scheduler/AgentRuntime/MissionEngine/MemoryProvider dans "
              "cette étape — `ArtifactMetadata.memory_record_id` est le "
              "seul point d'association prévu, jamais une duplication de "
              "la provenance déjà portée par `MemoryRecord`.",
    ),
    Abstraction(
        "SandboxProvider", "exécution isolée de code généré", 3, 3,
        ImplStatus.STUB, "aucun — refuse l'exécution si l'isolation n'est pas vérifiée",
        module="apex.sandbox.provider",
        notes="STUB et non IMPLEMENTED délibérément : niveau NAMESPACED atteint et "
              "vérifié empiriquement (namespaces Linux mount/pid/net/ipc/uts + cgroups "
              "v1 cpu/memory/pids + abaissement de privilège réel via setpriv, pas un "
              "simple remap de namespace + racine remontée en lecture seule + réseau "
              "coupé, y compris la boucle locale vers PostgreSQL — tout testé par "
              "intégration réelle sur cette machine, jamais supposé). JAMAIS "
              "« container-grade » ni « fully isolated » : aucun jail de filesystem "
              "complet (pas de pivot_root vers un rootfs minimal, un fichier "
              "monde-lisible de l'hôte reste lisible) et aucun seccomp-bpf (libseccomp.so "
              "présente sur cette machine mais aucun binding Python — non exploitable par "
              "ce code). `LinuxNamespaceSandbox` est le seul backend "
              "implémenté ; `ContainerSandbox` reste PLANNED (démon Docker injoignable "
              "ici). `detect_capabilities()` sonde empiriquement à chaque appel — sur une "
              "machine différente (moins capable OU plus capable), le niveau réellement "
              "obtenu peut différer ; ce résultat ne doit jamais être extrapolé à une "
              "autre machine, voir apex.config.schema.PoolStatus (CURRENT vs TARGET). "
              "GATE-19 (apex.gate.checks.gate_19_isolation_state) consulte désormais "
              "cette même détection quand Docker n'atteint ni docker_hardened ni "
              "gvisor (ADR-0012) : NAMESPACED y est reconnu comme un niveau distinct, "
              "jamais confondu avec un plafond conteneur — la Gate ne sous-déclare "
              "plus cette capacité, sans jamais accorder red_team_allowed pour autant.",
    ),
    Abstraction(
        "ComputePool", "où s'exécute un run (GPU/CPU/local/distant/cloud)", 1, 6,
        ImplStatus.DEFINED, "pool unique « local »",
        module="apex.config.schema",
        notes="Principe 6 : déclaré dès la phase 1 dans la configuration.",
    ),
    Abstraction(
        "Placement", "assignation run -> pool", 6, 6,
        ImplStatus.PLANNED, "pool unique",
    ),
    Abstraction(
        "ToolProvider", "outils à effet de bord", 7, 7,
        ImplStatus.PLANNED, "fs, git, shell",
    ),
    Abstraction(
        "ToolRegistry", "catalogue et métadonnées d'outils", 7, 7,
        ImplStatus.PLANNED, "aucun",
    ),
    Abstraction(
        "PolicyEngine", "autorisation avant tout effet de bord", 2, 7,
        ImplStatus.IMPLEMENTED, "allow-list statique",
        module="apex.policy.engine",
        notes="Deux couches : interdits absolus (jamais accordables, à aucun "
              "sujet) puis allow-list par sujet fermée, refus par défaut. "
              "Sans dépendance (pas de DB) — la décision est journalisée par "
              "l'appelant, pas par le moteur. Pas encore de consommateur réel "
              "(AgentRuntime, phase 7) : le moteur est utilisable et testé "
              "isolément, comme EventSink avant son branchement.",
    ),
    Abstraction(
        "SecretBroker", "injection de secrets hors contexte modèle", 2, 7,
        ImplStatus.STUB, "variables d'environnement uniquement",
        module="apex.secrets.provider",
        notes="STUB et non IMPLEMENTED délibérément : `EnvSecretBroker` est "
              "réel — `resolve()` lit une variable d'environnement "
              "réellement, dans un espace de noms dédié "
              "(`APEX_SECRET_<CLÉ>`, jamais partagé avec `APEX_DB_*_PASSWORD`/"
              "`POSTGRES_PASSWORD`), lève `SecretNotFound` (jamais une "
              "valeur vide ou devinée) si absent ou vide. `has()` sonde sans "
              "lever, pour un futur routeur qui doit décider entre "
              "plusieurs providers sans se bloquer sur celui qui n'est pas "
              "configuré. Aucune valeur n'est jamais journalisée par ce "
              "module (`apex.obs.logging.redact()` reste la défense en "
              "profondeur si un appelant faisait l'erreur). **Désormais "
              "réellement consommé** : `OpenAICompatibleProvider` "
              "(`apex.models.openai_compatible`) résout `ModelEndpoint."
              "secret_key` via ce broker à chaque appel, jamais mis en "
              "cache — testé contre un serveur HTTP de test qui vérifie "
              "réellement l'en-tête `Authorization` reçu (secret manquant "
              "-> aucune tentative HTTP ; mauvais secret -> rejeté par le "
              "serveur, jamais simulé comme accepté). Restent `PLANNED` : "
              "fichier chiffré local, rotation, secret "
              "manager distant — des backends supplémentaires du même "
              "contrat, jamais un second contrat.",
    ),
    Abstraction(
        "MemoryProvider", "écriture/récupération avec provenance", 5, 8,
        ImplStatus.STUB, "PostgreSQL + pgvector",
        module="apex.memory.service",
        notes="STUB et non IMPLEMENTED délibérément — tranche minimale de "
              "l'étape 9 (docs/COGNITIVE_ARCHITECTURE.md §6) : "
              "`MemoryRecord` (provenance, source, confiance `[0,1]`, statut "
              "de vérification `VerificationStatus` — VERIFIED/LIKELY/"
              "UNCERTAIN/DISPUTED/FALSE, distinct du `confidence` numérique "
              "et de `apex.obs.status.DataStatus`, jamais fusionnés) et "
              "`MemoryRelation` (SUPPORTS/CONTRADICTS entre deux "
              "`MemoryRecord` EXISTANTS) sont réels, testés contre "
              "PostgreSQL réel, jamais mutés en place (une révision est une "
              "nouvelle ligne via `supersedes_id`, chaîne strictement "
              "linéaire ; la seule mutation en place est la visibilité "
              "OWN -> SHARED via `promote_to_shared`, un acte explicite et "
              "audité, accordé à personne par défaut). Réutilise "
              "intégralement PolicyEngine existant (WRITE_OWN_MEMORY/"
              "READ_SHARED_MEMORY/PROMOTE_MEMORY_TO_SHARED), aucune action "
              "ajoutée. Ce qui reste PLANNED : `EmbeddingProvider` (RAG, "
              "recherche sémantique — abstraction séparée, non touchée), "
              "aucun moteur de traversée de graphe ni de visualisation "
              "(les arêtes sont une structure de données, pas un graphe "
              "interrogeable), aucun raccordement automatique depuis "
              "`MissionEngine`/`AgentRuntime` (un futur pas, pas celui-ci).",
    ),
    Abstraction(
        "GenomeStore", "configuration mutable versionnée", 4, 17,
        ImplStatus.STUB, "génome unique figé",
        module="apex.genome.service",
        notes="STUB et non IMPLEMENTED délibérément (Phase 17) : "
              "`GenomeStoreService.propose()` compose RÉELLEMENT "
              "`ArtifactStoreProvider` (STUB réel depuis la Phase 10) pour "
              "le contenu — sérialisation JSON canonique (clés triées, "
              "déterministe : même contenu -> même `content_hash`, prouvé "
              "par test), jamais réimplémenté. `content_hash` EST le "
              "`genome_ref: hash` déjà spécifié pour `Clone` "
              "(`docs/COGNITIVE_ARCHITECTURE.md` §2.2) — aucun nouveau "
              "format d'identifiant inventé. Cycle de décision : "
              "`Action.PROPOSE_GENOME_VERSION` (CORE et HUMAIN) puis "
              "`PROMOTE_GENOME_VERSION`/`REJECT_GENOME_VERSION` (**HUMAIN "
              "seul**, décision UNIQUE, jamais redécidée), EXACTEMENT le "
              "patron `SkillRegistryService` (Phase 14) — cycle HUMAIN "
              "provisoire en attendant `PromotionGovernor` (`PLANNED`, "
              "phase 16, périmètre jugé insuffisamment défini). "
              "`get_current()` dérive la version active de la décision la "
              "plus récente (`decided_at`), jamais un pointeur mutable "
              "séparé — source d'incohérence évitée par construction. "
              "**Invariant vérifié** : `Subject.RESEARCH` (tout type de "
              "clone) ne peut ni proposer ni promouvoir/rejeter — testé "
              "explicitement, PLUS testé contre PostgreSQL réel "
              "(`apex_agent` sans aucun GRANT sur `genome_versions`). "
              "**Ne modifie jamais le Core** : stocke des DONNÉES (dict "
              "JSON), jamais de code ; aucun raccordement à "
              "`AgentRuntime`/`Clone.genome_ref` — un futur pas, pas "
              "celui-ci.",
    ),
    Abstraction(
        "MutationOperator", "opérateurs d'évolution sur le Genome", 4, 17,
        ImplStatus.STUB, "aucun",
        module="apex.genome.mutation",
        notes="STUB et non IMPLEMENTED délibérément (Phase 17) : "
              "`mutate()` (perturbation ponctuelle de chaque feuille "
              "numérique — int/float — avec probabilité `rate`, inversion "
              "des booléens à la même probabilité, jamais les chaînes ni "
              "`None` — « mutations structurales appropriées au type ») ET "
              "`crossover()` (croisement uniforme récursif entre deux "
              "parents) sont réels et testés, tous deux déterministes pour "
              "une graine donnée. Directive Phase 17 explicite suivie à la "
              "lettre : « le crossover doit être explicitement supporté "
              "lorsque MutationOperator sera implémenté » — pas de "
              "`RecombinationEngine` séparé. Fonctions PURES sur des `dict` "
              "ordinaires, zéro couplage à `apex.genome.service`/DB — "
              "produire un contenu muté/croisé reste une opération sur des "
              "DONNÉES, jamais automatiquement proposé ni promu (l'appelant "
              "doit explicitement invoquer `GenomeStoreService.propose"
              "(origin=MUTATION/CROSSOVER, ...)`).",
    ),
    Abstraction(
        "TaskBank", "banque de tâches et splits", 5, 5,
        ImplStatus.STUB, "banque statique",
        module="apex.tasks.service",
        notes="STUB et non IMPLEMENTED délibérément (Phase 5) : "
              "`TaskBankService.create()` persiste réellement (table `tasks`, "
              "INSERT seul — `UPDATE`/`DELETE`/`TRUNCATE` révoqués en "
              "migration pour tous les rôles applicatifs), autorise "
              "`Action.CREATE_TASK` (HUMAN seul, `PolicyEngine`, même motif "
              "que `CREATE_MISSION` — un clone ne peut jamais écrire sa "
              "propre tâche), valide la spécialité "
              "(`apex.specialties.registry`). Split HELDOUT (ADR-0010, "
              "différé phase 2) réellement appliqué : le rôle SQL `apex_eval` "
              "n'a AUCUN grant sur `tasks` (migration `eda413ca0829`) — un "
              "exécuteur d'évaluation ne peut structurellement pas lire une "
              "tâche, HELDOUT ou non, qu'il connaisse ou non son `id` ; "
              "`tests/test_db_security.py::TestHeldoutSplitSeparation` "
              "vérifie ceci contre PostgreSQL réel, plus un test unitaire "
              "SQLite. Pas de champ `method` sur `Task` (délibéré — voir "
              "`apex.db.models.TaskRow`), pas de `TaskGenerator` (PLANNED, "
              "phase 15) : les tâches de cette étape sont écrites à la main "
              "par un HUMAN, jamais générées.",
    ),
    Abstraction(
        "OracleProvider", "vérité terrain d'une tâche", 5, 5,
        ImplStatus.STUB, "oracle « tests »",
        module="apex.oracles.provider",
        notes="STUB et non IMPLEMENTED délibérément (Phase 5) : seul "
              "`ExitCodeOracle` existe — jugement par `ExecutionStatus` du "
              "`SandboxResult` (SUCCESS/FAILED déjà classifiés par code de "
              "sortie réel dans `LinuxNamespaceSandbox`), mappé sur les cinq "
              "catégories exigées : PASS (SUCCESS), FAIL (FAILED), TIMEOUT, "
              "ERROR (échec d'infrastructure — OOM/limite ressource/réseau "
              "refusé/permission refusée, jamais confondu avec un FAIL de "
              "correction), UNKNOWN (annulation, ou statut non reconnu — "
              "jamais un verdict deviné). Aucun oracle par comparaison de "
              "sortie attendue, aucun jugement par `ModelProvider` — le "
              "contrat prend déjà `task` en paramètre pour ne pas avoir à "
              "changer de signature le jour où un tel oracle existera.",
    ),
    Abstraction(
        "EvalRunner", "exécution appariée, seeds, budgets", 5, 5,
        ImplStatus.STUB, "séquentiel local",
        module="apex.evalrunner.service",
        notes="STUB et non IMPLEMENTED délibérément (Phase 5) : "
              "`EvalRunnerService.run()` compose réellement TaskBank + "
              "`AgentRuntime.execute_in_sandbox` (isolation NAMESPACED "
              "réelle, jamais simulée) + `OracleProvider`, persiste chaque "
              "résultat (table `eval_runs`, INSERT seul, positif ET négatif "
              "retenus de façon identique — aucun filtrage à l'écriture). "
              "Champ `method` (vocabulaire fermé "
              "`docs/COGNITIVE_ARCHITECTURE.md` §5, `EvalMethod`) taggé sur "
              "chaque run dès cette étape — collecte pure, AUCUNE "
              "comparaison entre méthodes (réservée à `MetaLearner`, "
              "PLANNED, phase 22). `seed`/`dataset_ref`/`experiment_ref` "
              "portés comme simples colonnes (données, pas de logique) — "
              "affordances explicites pour que les phases 12/14/15/16/17/18 "
              "futures (plusieurs Clones par expérience, comparaison de "
              "`genome_ref`, etc.) n'exigent aucune migration de schéma "
              "supplémentaire pour exister, sans qu'aucune de ces phases ne "
              "soit implémentée ici. Le chemin déterministe ci-dessus "
              "(`run()`) reste inchangé. Passe « Real Model Evaluation » "
              "(additive) : `EvalRunnerService.run_model_eval()` ajoute un "
              "second chemin, `EvalRun.model_id` nullable (NULL sur tout "
              "run déterministe existant, jamais deviné) — TASK -> "
              "`ModelProvider` (abstrait, résolu via "
              "`apex.models.provider.get_default_provider`, AUCUN couplage "
              "direct à `OllamaProvider` depuis ce module) -> "
              "`GenerationResult` persisté (texte borné à 50 000 "
              "caractères, DONNÉE jamais exécutée) -> "
              "`ResponseJudge.judge()` (contrat `apex.oracles.response_judge`, "
              "implémentation par défaut `ExactMatchResponseJudge` — "
              "comparaison déterministe à `task.expected_answer`, "
              "`UNKNOWN` si absente, jamais de second LLM juge). Un échec "
              "`ModelProvider` (connexion, timeout) est persisté comme "
              "`EvalRun` ERROR avec `model_id=None`, jamais une exception "
              "remontée à l'appelant. AUCUNE promotion, aucune "
              "modification de Genome — un laboratoire d'évaluation, pas un "
              "système autonome d'amélioration. `exécution appariée` "
              "(comparaison A/B contrôlée), budgets, routing "
              "exploration/exploitation et jugement multi-tier restent "
              "PLANNED, non implémentés : un `EvalRun` (déterministe ou "
              "modèle) est une tentative indépendante, jamais encore mise "
              "en regard d'une autre par ce module — STUB inchangé.",
    ),
    Abstraction(
        "StatTest", "décision statistique, preuve cumulable", 5, 16,
        ImplStatus.STUB, "bootstrap apparié",
        module="apex.stattest.comparator",
        notes="STUB et non IMPLEMENTED délibérément (Phase 16) : "
              "`StatTest.compare_paired()` calcule un VRAI intervalle de "
              "confiance par bootstrap apparié (stdlib `random`, ADR-0002 — "
              "aucune dépendance `numpy`/`scipy`), déterministe pour une "
              "graine donnée, testé sur des cas clairement significatifs, "
              "clairement non significatifs, et la reproductibilité. "
              "Principe 10 (« validité continue, accumulation multi-nuits ») "
              "satisfait PAR CONSTRUCTION : aucun état conservé entre deux "
              "appels, ré-exécuter avec plus de paires produit une "
              "nouvelle décision indépendante, jamais une mise à jour d'un "
              "état figé. **Remplace explicitement** l'heuristique fixe "
              "documentée dans `apex.weakness.detector` "
              "(`CREDIBLE_FAILURE_RATE_THRESHOLD`, qui se déclarait déjà "
              "elle-même « PAS un StatTest réel ») pour tout appelant qui a "
              "besoin d'une vraie décision statistique — `WeaknessDetector` "
              "lui-même n'est PAS modifié ici (contrat déjà validé, Phase "
              "12), ce module fournit seulement l'outil qui n'existait pas "
              "encore. Aucun raccordement automatique à `WeaknessDetector`/"
              "`CurriculumStrategy`/`MetaLearner` : un outil composable, pas "
              "encore composé.",
    ),
    Abstraction(
        "JudgeTier", "notation hiérarchisée tier 0/1/2", 5, 9,
        ImplStatus.STUB, "tier 0 objectif seul",
        module="apex.judge.provider",
        notes="STUB et non IMPLEMENTED délibérément (Phase 9) : mapping "
              "`docs/VISION_INTEGRATION.md` §16 — « le Verifier est le tier "
              "0/1 ; le Critic est un rôle d'agent défini dans le Genome ». "
              "Seul `Tier0Judge` existe réellement : délègue INTÉGRALEMENT "
              "à `OracleProvider.judge()` (réel depuis la Phase 5, "
              "`ExitCodeOracle`), zéro logique de jugement dupliquée — "
              "`JudgeVerdict` enveloppe un `OracleResult` tel quel, ne "
              "réinvente aucun second vocabulaire de correction. TIER_1 "
              "(vérification automatisée étendue) et TIER_2 (Critic Clone, "
              "`Subject.CRITIC` déjà existant, verdict consultatif qui ne "
              "bloque jamais seul) restent `PLANNED` : le contrat "
              "(`JudgeTierLevel`) nomme déjà les trois paliers, mais aucune "
              "escalade n'est jamais tentée — un verdict `UNKNOWN` de tier 0 "
              "reste `UNKNOWN`, jamais « résolu » par un palier qui n'existe "
              "pas. Aucun raccordement à `AgentRuntime`/`Subject.CRITIC` : "
              "TIER_2 n'exécute aucun Clone ici. `JudgeTier` n'est PAS encore "
              "utilisé par `EvalRunner` (qui continue d'appeler "
              "`OracleProvider` directement, Phase 5) — décision de "
              "périmètre explicite, pas un oubli : ce raccordement est un "
              "choix architectural séparé, à valider indépendamment (même "
              "discipline que ModelProvider↔MissionEngine, Phase 6).",
    ),
    Abstraction(
        "LanguageToolchain", "build/test/lint/bench par langage", 3, 6,
        ImplStatus.DEFINED, "Python d'abord",
        module="apex.config.schema",
        notes="C++ en vague 2 (phase 15), métriques séparées.",
    ),
    Abstraction(
        "SearchProvider", "recherche web pour le Research Agent", 4, 13,
        ImplStatus.PLANNED, "désactivé",
    ),
    Abstraction(
        "WeaknessDetector", "détection d'écarts de compétence", 5, 12,
        ImplStatus.STUB, "requête SQL simple",
        module="apex.weakness.service",
        notes="STUB et non IMPLEMENTED délibérément (Phase 12) : "
              "`WeaknessDetectorService.detect_for_specialty()` rassemble "
              "réellement les `EvalRun` persistés (Phase 5) des tâches d'une "
              "spécialité (`Task.specialty`, seul proxy de « compétence/"
              "domaine » disponible — `SkillRegistry`, PLANNED, phase 14, "
              "n'existe pas), filtrable par `clone_id`. `WeaknessDetector` "
              "(logique pure, `apex.weakness.detector`) distingue les cinq "
              "catégories exigées : `INSUFFICIENT_DATA`/`NO_WEAKNESS`/"
              "`ISOLATED_FAILURE`/`REPEATED_WEAKNESS`/"
              "`STATISTICALLY_CREDIBLE_WEAKNESS`, sur la base des "
              "`oracle_verdict` réels (`OracleProvider`, Phase 5) — ERROR/"
              "TIMEOUT/UNKNOWN systématiquement exclus du calcul de "
              "faiblesse, jamais confondus avec un échec de tâche. Chaque "
              "rapport cite `evidence_run_ids` (traçabilité complète). "
              "**Limite honnête** : le seuil « statistiquement crédible » "
              "est une HEURISTIQUE fixe documentée (taux d'échec >= 50 % sur "
              "n >= 5), PAS un `StatTest` réel (`StatTest` reste PLANNED, "
              "phase 16) — ne jamais présenter ce mot comme une preuve "
              "statistique rigoureuse. Aucune traversée de Knowledge Graph "
              "(`MemoryProvider` relations, mentionnée par "
              "`docs/COGNITIVE_ARCHITECTURE.md` §4) : seul `EvalRunner` est "
              "exploité pour l'instant.",
    ),
    Abstraction(
        "CurriculumStrategy", "progression de difficulté", 5, 12,
        ImplStatus.STUB, "difficulté fixe",
        module="apex.curriculum.strategy",
        notes="STUB et non IMPLEMENTED délibérément (Phase 12) : "
              "`CurriculumStrategy.decide()` applique une règle DÉTERMINISTE "
              "réelle (même `WeaknessReport` -> toujours la même "
              "`CurriculumDecision`) sur un niveau ENTIER ABSTRAIT "
              "(`1..max_level`) — jamais plus d'un cran par décision "
              "(ADVANCE si `NO_WEAKNESS`, REINFORCE si "
              "`STATISTICALLY_CREDIBLE_WEAKNESS`, HOLD sinon), bornes "
              "respectées. **Décision de périmètre explicite** : le niveau "
              "de difficulté n'est PAS un champ ajouté à `apex.tasks.model."
              "Task` (aucune modification du contrat Phase 5 déjà validé) — "
              "la correspondance « niveau N -> quelles tâches concrètes » "
              "reste PLANNED, propriété future de `TaskGenerator` (phase "
              "15) ; ce module ne choisit JAMAIS de tâche, seulement une "
              "direction. Aucune dépendance à `MetaLearner` (PLANNED, phase "
              "22) : la politique adaptative/apprise reste future.",
    ),
    Abstraction(
        "TaskGenerator", "self-play, bug farm, adversarial", 5, 15,
        ImplStatus.STUB, "banque statique",
        module="apex.taskgen.provider",
        notes="STUB et non IMPLEMENTED délibérément (Phase 15) : "
              "`DeterministicTaskGenerator` couvre RÉELLEMENT cinq des onze "
              "`GenerationMode` (directive Phase 15), tous pour la seule "
              "spécialité `is_supported` (`research`) : `NORMAL` (réutilise "
              "`apex.missions.workloads.research_diagnostic_command()`, "
              "zéro duplication), `INCREASING_DIFFICULTY`/"
              "`CURRICULUM_INTERMEDIATE` (script paramétré par un niveau "
              "1..3, où le niveau 3 fait RÉELLEMENT échouer le script "
              "— `sys.exit(1)` — si l'isolation réseau attendue est "
              "violée : la « difficulté » n'est pas un label, prouvée par "
              "test contre un vrai sandbox namespaced — échoue hors "
              "sandbox, réussit dedans, exactement l'inverse), "
              "`TARGETED_WEAKNESS` (niveau dérivé de la sévérité d'un "
              "`apex.weakness.model.WeaknessReport` réel, Phase 12 — cible "
              "la sévérité, PAS un sous-contrôle précis, limite "
              "explicitement documentée), `REGRESSION` (reproduit la "
              "commande d'une `Task` existante à l'identique, jamais un "
              "contenu réinventé). **Aucun `ModelProvider`** : chaque "
              "script est déterministe, stdlib pur, aucune inférence. "
              "**Aucune persistance** : `GeneratedTaskSpec` reste en "
              "mémoire — la création reste `TaskBankService.create()` "
              "(HUMAN seul), jamais automatique, jamais un raccordement "
              "TaskGenerator -> TaskBank qui contournerait le contrôle "
              "humain établi en Phase 5. Les six autres modes "
              "(`EDGE_CASE`/`BUG_FARM`/`ADVERSARIAL`/`TRANSFER`/`SELF_PLAY`/"
              "`FROM_HYPOTHESIS`) lèvent `apex.errors.NotImplementedYet`, "
              "chacun citant sa dépendance réellement bloquante "
              "(`CodeIntelligence` PLANNED phase 12, `HypothesisGenerator` "
              "PLANNED phase 16, absence d'une seconde spécialité réelle, "
              "absence d'orchestration multi-clones) — jamais un contenu "
              "inventé pour combler l'absence de dépendance.",
    ),
    Abstraction(
        "SelectionStrategy", "successive halving, front de Pareto", 4, 17,
        ImplStatus.STUB, "candidat unique",
        module="apex.genome.selection",
        notes="STUB et non IMPLEMENTED délibérément (Phase 17) : "
              "`successive_halving()` (garde la fraction la mieux notée "
              "d'UN round — enchaîner plusieurs rounds à budget croissant "
              "reste un futur orchestrateur, `ExperimentEngine`, composite "
              "documenté jamais construit ici) et `pareto_front()` "
              "(domination standard, tous les objectifs déjà orientés "
              "« plus grand est meilleur » par convention explicite) sont "
              "des fonctions PURES, réelles, testées — n'exécutent JAMAIS "
              "rien (ni Clone, ni Sandbox, ni EvalRunner) : les scores "
              "viennent toujours de l'appelant. Aucun raccordement à "
              "`Experiment.clones_participants`/`AgentRuntime` — la "
              "sélection est un outil composable, pas encore composé.",
    ),
    Abstraction(
        "MetaLearner", "apprendre comment apprendre", 5, 22,
        ImplStatus.STUB, "journalisation seule",
        module="apex.metalearner.selector",
        notes="STUB et non IMPLEMENTED délibérément (Phase 22) : "
              "`MetaLearner.select_strategy()` implémente EXACTEMENT la "
              "« politique statique/simple » actée dès la validation "
              "initiale du Learning System (« avant Phase 22, Learning "
              "Manager utilise une politique statique ; à partir de "
              "MetaLearner, le choix devient progressivement appris ») — "
              "ici, la politique statique elle-même, PAS encore "
              "l'apprentissage réel. Compose `CurriculumStrategy.decide()` "
              "(Phase 12, réel) pour le niveau et réutilise DIRECTEMENT "
              "`apex.taskgen.model.GenerationMode` (Phase 15, réel) pour la "
              "stratégie — aucun second vocabulaire de stratégie, aucune "
              "seconde logique de progression dupliquée. Règle "
              "déterministe, testée sur les cinq catégories de "
              "`WeaknessCategory` : `INSUFFICIENT_DATA` -> aucune stratégie "
              "recommandée (jamais devinée) ; `NO_WEAKNESS` -> "
              "`CURRICULUM_INTERMEDIATE` (progression) ; les trois "
              "catégories de faiblesse -> `TARGETED_WEAKNESS` (dimensionné "
              "par `TaskGenerator` lui-même, aucun second mapping sévérité "
              "-> niveau). **Pourquoi aucun apprentissage réel n'existe "
              "encore, et ce n'est pas un oubli** : apprendre « quelle "
              "stratégie fonctionne » exigerait de savoir, pour chaque "
              "`EvalRun` passé, quel `GenerationMode` a produit sa `Task` — "
              "ce lien n'est persisté nulle part (`EvalRun.method` est "
              "`EvalMethod`, un vocabulaire distinct, jamais fusionné avec "
              "`GenerationMode`) ; l'ajouter modifierait le contrat déjà "
              "validé d'`EvalRun` (Phase 5), hors périmètre. "
              "**Exploration/exploitation** : appartient conceptuellement à "
              "`MetaLearner` (aucun composant séparé créé), mais non "
              "implémentée pour la même raison — aucune donnée historique "
              "de performance par stratégie à arbitrer aujourd'hui. Aucun "
              "état conservé entre deux appels (prouvé par test) : v1 est "
              "une fonction pure du `WeaknessReport` courant, jamais une "
              "mémoire simulée.",
    ),
    Abstraction(
        "EventSink", "journal d'événements append-only", 1, 2,
        ImplStatus.IMPLEMENTED, "logging structuré JSON (sink additionnel, pas un remplacement)",
        module="apex.db.event_sink",
        notes="Persistance PostgreSQL (phase 2, apex.db.event_sink.DatabaseEventSink). "
              "Validé contre PostgreSQL réel (tests/test_db_security.py) ; le sink JSON "
              "structuré (apex.obs.logging) reste actif en parallèle, inchangé.",
    ),
    Abstraction(
        "MetricSink", "métriques de tout composant", 1, 1,
        ImplStatus.IMPLEMENTED, "registre en processus",
        module="apex.obs.metrics",
    ),
    Abstraction(
        "HealthCheckable", "contrat de santé de tout composant", 1, 1,
        ImplStatus.IMPLEMENTED, "aucun",
        module="apex.obs.health",
    ),
    Abstraction(
        "CheckpointStore", "reprise d'expérience longue durée", 2, 19,
        ImplStatus.PLANNED, "état en base",
        notes="Principe 8 : une expérience interrompue reprend à son état sauvegardé. "
              "**Analysé et délibérément reporté à la Phase 18/19 (directive : "
              "« si son périmètre est suffisamment spécifié »)** : jugé NON "
              "suffisamment spécifié — aucun processus long-cours "
              "(`ExperimentEngine`) n'existe encore comme code exécutable "
              "(composite documenté, jamais une classe) qui aurait besoin "
              "d'être interrompu puis repris ; construire `CheckpointStore` "
              "maintenant reviendrait à deviner un mécanisme de reprise "
              "pour un processus qui n'existe pas. Le fallback honnête "
              "« état en base » est déjà, en pratique, ce qui existe "
              "réellement : chaque `EvalRun` (Phase 5) est persisté "
              "immédiatement, INSERT seul — une campagne d'évaluations "
              "interrompue se reprend déjà en interrogeant "
              "`EvalRunRepository.list_by_task`/`list_by_specialty` et en "
              "sautant ce qui est déjà enregistré, sans abstraction dédiée. "
              "Même raisonnement que le report de `PromotionGovernor` "
              "(Phase 16) : ne pas construire une coquille sans "
              "consommateur réel.",
    ),
    # -- Ajouts issus de la Master Architecture Specification -----------------
    # Chacune a été confrontée au registre existant (§58) : elles sont ajoutées
    # parce qu'aucune abstraction en place ne les couvre sans dénaturer son rôle.
    Abstraction(
        "SkillRegistry", "compétences versionnées, graphe de prérequis, promotion", 11, 14,
        ImplStatus.STUB, "aucune compétence — capacités portées par le Genome",
        module="apex.skills.service",
        notes="STUB et non IMPLEMENTED délibérément (Phase 14) : "
              "`SkillRegistryService.propose()` persiste réellement (table "
              "`skills`), autorise `Action.PROPOSE_SKILL` (CORE et HUMAN, "
              "jamais un clone), valide la spécialité, le graphe de "
              "prérequis (existence + absence de cycle — réutilise "
              "`apex.missions.dependencies.detect_cycle` telle quelle, "
              "aucune logique dupliquée, prouvé câblé par un test de cycle "
              "manufacturé, même patron que "
              "`test_missions_service.py::TestDependsOn`) et les "
              "`evidence_run_ids` (chaque preuve doit référencer un "
              "`EvalRun` RÉELLEMENT persisté — Phase 5 — sinon rejetée, "
              "jamais une preuve inventée). `promote()`/`reject()` : "
              "**HUMAIN seul** (`Action.PROMOTE_SKILL`/`REJECT_SKILL`), "
              "décision UNIQUE (même garde que "
              "`MissionService._require_no_prior_decision`), promotion "
              "bloquée tant que les prérequis ne sont pas eux-mêmes "
              "`ACQUIRED` (vérifié réellement, testé). `revise()` implémente "
              "la « généalogie » : insère une NOUVELLE ligne "
              "(`supersedes_id`), jamais une mutation du contenu — même "
              "patron que `MemoryService.revise`/`find_successor`, chaîne "
              "linéaire garantie. Cycle de promotion **HUMAIN**, "
              "explicitement DISTINCT de `PromotionGovernor` (`PLANNED`, "
              "phase 16, qui gouvernera la promotion vers le Core — un "
              "sujet entièrement différent, jamais mélangé ici) : "
              "`SkillRegistry` ne promeut qu'une entrée du registre de "
              "compétences, aucun chemin vers le Core. **Hors périmètre** : "
              "`confidence` n'est jamais calculée ici — un appelant peut la "
              "fournir (ex. déjà produite par "
              "`apex.calibration.ConfidenceCalibrator`, Phase 12) mais ce "
              "module ne l'invoque pas lui-même ; aucun raccordement à "
              "`Self-Model` (vue composée future) ni à `MemoryProvider`/"
              "Knowledge Graph.",
    ),
    Abstraction(
        "PromotionGovernor", "autorité de promotion indépendante du système évalué", 16, 16,
        ImplStatus.PLANNED, "décision par l'ExperimentEngine, journalisée",
        notes="APEX ne doit pas être seul juge de sa propre promotion. Hors de "
              "portée de toute mutation, au même titre que le Policy Engine. "
              "**Réévalué après la passe de capitalisation** (`GenomeStore` "
              "et `SkillRegistry` sont désormais RÉELS, plus seulement "
              "`PLANNED` — l'ancien motif de report, « GenomeStore n'existe "
              "pas encore », n'est donc plus valable) : toujours PLANNED, "
              "mais pour une raison DIFFÉRENTE et actualisée, pas la même "
              "laissée par oubli. `PolicyEngine.authorize(Subject.HUMAN, "
              "Action.PROMOTE_SKILL/PROMOTE_GENOME_VERSION)` EST DÉJÀ, "
              "structurellement, l'autorité de promotion indépendante que "
              "`PromotionGovernor` devait fournir : deux consommateurs "
              "indépendants (`SkillRegistryService.promote`, "
              "`GenomeStoreService` via `GenomeVersionRepository.decide`) "
              "appliquent CHACUN, sans se connaître, la même discipline "
              "(décision HUMAIN seule, unique, jamais auto-décidée, aucun "
              "chemin alternatif — `PolicyEngine` est le seul point "
              "d'application, déjà interdit à toute boucle d'apprentissage). "
              "`SkillRegistry` a démontré, de façon indépendante, le patron "
              "« proposition CORE/HUMAIN + décision HUMAIN unique » que "
              "`PromotionGovernor` aurait dû généraliser — `GenomeStore` "
              "l'a ensuite reproduit à l'identique sans jamais consulter "
              "`SkillRegistry`, preuve que le patron généralise déjà "
              "correctement PAR RÉPÉTITION, sans classe commune. Ajouter "
              "une classe `PromotionGovernor` aujourd'hui échouerait le "
              "critère « aucun duplicata » du test en 4 points : elle "
              "n'envelopperait que des vérifications déjà correctement "
              "centralisées dans `PolicyEngine`, sans responsabilité "
              "réellement nouvelle à porter. Resterait PLANNED jusqu'à un "
              "TROISIÈME consommateur (ex. une future promotion de Memory "
              "vers `SHARED` gouvernée au même niveau) dont le besoin "
              "générique deviendrait alors démontrable, pas seulement "
              "supposé — un troisième point de répétition, pas une "
              "anticipation.",
    ),
    Abstraction(
        "HypothesisGenerator", "observation -> hypothèse falsifiable", 16, 16,
        ImplStatus.STUB, "hypothèses saisies par l'humain",
        module="apex.hypothesis.generator",
        notes="STUB et non IMPLEMENTED délibérément (Phase 16) : "
              "`HypothesisGenerator.generate()` compose UNIQUEMENT "
              "`apex.weakness.model.WeaknessReport` (réel, Phase 12) — "
              "zéro `ModelProvider`, contrairement à ce que ce composant "
              "visera à terme (« seul composant où un modèle externe "
              "apporte un gain net » reste vrai, non encore exploité, même "
              "discipline que `apex.taskgen`, Phase 15). Chaque hypothèse "
              "produite est réellement falsifiable : `prediction` nomme un "
              "chemin de réfutation concret et RÉEL — "
              "`apex.taskgen.model.GenerationMode.TARGETED_WEAKNESS` (Phase "
              "15) pour produire les tâches de renforcement, "
              "`apex.stattest.StatTest.compare_paired` (Phase 16, ce même "
              "tour) pour juger l'amélioration — jamais exécutés par ce "
              "module (prouvé par test : aucun import de `apex.evalrunner`/"
              "`apex.agents.runtime`/`apex.sandbox`). « Distinct de "
              "l'ExperimentEngine, qui exécute et décide mais ne propose "
              "pas » : `ExperimentEngine` reste le composite documenté "
              "(TaskBank+TaskGenerator+HypothesisGenerator+OracleProvider+"
              "JudgeTier+EvalRunner+StatTest+Memory+sélection), jamais une "
              "classe séparée. **Jamais persistée** : aucune table "
              "`hypotheses` — aucun consommateur réel "
              "(`ExperimentEngine`) n'existe encore pour la lire (ADR-0007).",
    ),
    Abstraction(
        "CostGainEstimator", "gain attendu / coût de calcul, puis calibration sur le réel", 5, 18,
        ImplStatus.STUB, "coût réel mesuré, gain non estimé",
        module="apex.costgain.estimator",
        notes="STUB et non IMPLEMENTED délibérément (Phase 18, enrichi lors "
              "de la passe de capitalisation) : `CostGainEstimator."
              "estimate_cost()` additionne le VRAI `elapsed_s` de chaque "
              "`EvalRun` (Phase 5) — `UNKNOWN`, jamais un sous-total "
              "silencieux, si UN SEUL run manque cette mesure (prouvé par "
              "test). `memory_peak_mb` (cgroups v1 "
              "`memory.max_usage_in_bytes`, réel) est maintenant copié par "
              "`EvalRunnerService._build_eval_run` dans "
              "`EvalRun.facts[\"memory_peak_mb\"]` — le sac JSON libre déjà "
              "prévu par le contrat Phase 5, AUCUNE colonne ajoutée, aucune "
              "migration — puis lu par `estimate_cost()` comme le MAXIMUM "
              "(pas la somme, un pic n'est pas additif) sur les runs "
              "considérés, `UNKNOWN` si un seul run ne le porte pas. "
              "`cpu_time_s`/`token_count` restent honnêtement "
              "`Measurement.unknown()` — jamais 0 — pour deux raisons "
              "réelles et documentées, pas des oublis : (1) `cpu.stat` "
              "(cgroups v1) n'expose que du throttling, jamais le temps CPU "
              "réellement consommé — aucune valeur réelle à copier, quelle "
              "que soit la couche ; (2) `token_count` exigerait un "
              "raccordement `ModelProvider` -> `EvalRunner` qui n'existe "
              "pas (décision explicite depuis la Phase 5). `estimate_gain()`"
              " délègue INTÉGRALEMENT à "
              "`apex.stattest.StatTest.compare_paired` (Phase 16, réel) — "
              "zéro seconde logique statistique. `ratio` (gain/coût) n'est "
              "`Measurement.real` QUE si le gain est "
              "`StatTestVerdict.SIGNIFICANT` ET le coût temps réellement "
              "mesuré et positif — un gain `NOT_SIGNIFICANT` ou "
              "`INSUFFICIENT_DATA` donne toujours un ratio `UNKNOWN`, "
              "jamais un nombre construit sur du bruit statistique (prouvé "
              "par test, les deux cas). **Hors périmètre, honnêtement** : "
              "robustesse et transfert ne sont pas des champs de ce module "
              "— aucune infrastructure d'évidence cross-domaine/répétée "
              "n'existe encore pour les mesurer ; les ajouter comme champs "
              "systématiquement `UNKNOWN` aurait été un placeholder sans "
              "valeur, pas une honnêteté supplémentaire.",
    ),
    Abstraction(
        "ConfidenceCalibrator", "incertitude calibrée contre les résultats réels", 9, 12,
        ImplStatus.STUB, "seuils fixes dans le Genome",
        module="apex.calibration.calibrator",
        notes="STUB et non IMPLEMENTED délibérément (Phase 12) : "
              "`ConfidenceCalibrator.calibrate()` calcule un VRAI intervalle "
              "de Wilson à 95 % (méthode statistique standard, stdlib pur — "
              "ADR-0002) sur des compteurs succès/total réels, jamais un "
              "nombre décoratif. Trois notions jamais confondues : "
              "`observed_success_rate` (taux brut), "
              "`calibrated_point_estimate`/bornes (l'estimation calibrée, "
              "qui se resserre avec le volume — prouvé par test), "
              "`declared_confidence` (transmise telle quelle par l'appelant, "
              "jamais recalculée) — `calibration_gap` (déclaré - calibré) "
              "signe la sur/sous-confiance. Sous `n=3` : `INSUFFICIENT_DATA`, "
              "aucune valeur calibrée fabriquée. "
              "`calibrate_from_weakness_report()` réutilise EXACTEMENT "
              "l'échantillon PASS/FAIL de `WeaknessDetector` (Phase 12) — "
              "zéro définition divergente de ce qui compte comme résultat "
              "exploitable. **Hors périmètre** : aucun raccordement au "
              "Genome (`GenomeStore`, PLANNED, phase 17, n'existe pas) — "
              "« seuils fixes dans le Genome » reste le fallback documenté, "
              "pas encore réel ; ce module ne pilote encore aucune escalade "
              "de raisonnement/modèle, il calibre seulement, sur demande "
              "explicite d'un appelant.",
    ),
    # -- Ajouts issus de docs/COGNITIVE_ARCHITECTURE.md (ADR-0011) -------------
    Abstraction(
        "AgentRuntime", "instanciation, cycle de vie et isolation d'un clone spécialisé", 3, 7,
        ImplStatus.STUB, "exécution directe dans le Core (interactif uniquement)",
        module="apex.agents.runtime",
        notes="STUB et non IMPLEMENTED délibérément : cycle de vie, permissions "
              "(PolicyEngine), persistance (events/audit_log) et un type de "
              "clone (RESEARCH) validés de bout en bout et testés. Deux chemins "
              "d'exécution coexistent : run() reste sans isolation OS (thread "
              "intra-processus, budget détecté mais pas interrompu de force) ; "
              "execute_in_sandbox() (étape 7) délègue réellement à SandboxProvider "
              "(NAMESPACED, lui-même STUB — voir cette entrée) pour les missions "
              "lancées via apex.missions.execution.launch_mission. Le niveau "
              "NAMESPACED n'est ni container-grade ni seccomp — voir l'entrée "
              "SandboxProvider pour le détail exact des garanties tenues/non tenues.",
    ),
    Abstraction(
        "CodeIntelligence",
        "compréhension du dépôt APEX par lui-même : carte, dépendances, "
        "code↔test↔doc, détection d'incohérences et de lacunes", 7, 12,
        ImplStatus.PLANNED, "analyse manuelle par un humain",
        notes="Lecture et proposition seulement — ne modifie jamais le dépôt. "
              "Toute modification proposée passe par un Developer Clone "
              "(AgentRuntime) sous supervision complète : GATE, PromotionGovernor "
              "humain. Voir docs/COGNITIVE_ARCHITECTURE.md §8.",
    ),
    # -- Ajouts étape 6 — Mission Engine + Specialty Engine backend ------------
    Abstraction(
        "MissionEngine",
        "cycle de vie d'une mission — création, file d'attente, exécution, "
        "revue, décision humaine (valider/rejeter/réviser), annulation", 3, 7,
        ImplStatus.STUB, "aucune mission — coordination manuelle",
        module="apex.missions.execution",
        notes="STUB et non IMPLEMENTED délibérément : la machine à états "
              "(DRAFT/QUEUED/RUNNING/REVIEW/COMPLETED/FAILED/CANCELLED), "
              "PolicyEngine (aucun clone ne peut créer/valider/rejeter/lancer "
              "sa propre mission), la persistance PostgreSQL et la "
              "journalisation events/audit_log sont testées de bout en bout "
              "(y compris contre PostgreSQL réel). Depuis l'étape 7, "
              "apex.missions.execution.launch_mission relie réellement CORE -> "
              "POLICY -> AGENT RUNTIME -> SANDBOX -> MISSION -> RESULT -> REVIEW "
              "pour UNE seule charge de travail (diagnostic RESEARCH, strictement "
              "déterministe, apex.missions.workloads) — testé de bout en bout "
              "avec une vraie isolation NAMESPACED, y compris via "
              "POST /missions/{id}/launch (apex/api/routes/missions.py). "
              "`TaskBank`/`EvalRunner` (STUB depuis la phase 5, "
              "`apex.tasks`/`apex.evalrunner`) ne sont PAS raccordés à "
              "`MissionEngine` : aucun mécanisme ne dérive un « travail » "
              "arbitraire depuis l'objectif texte d'une mission quelconque "
              "— seul le diagnostic RESEARCH, câblé en dur, existe ici ; "
              "lancer toute autre spécialité échoue proprement. Une mission "
              "et un `EvalRun` restent deux chemins d'exécution distincts, "
              "jamais composés l'un dans l'autre par ce module.",
    ),
    Abstraction(
        "SpecialtyEngine",
        "registre backend des Specialty — capacités, outils, permissions "
        "requises, types de clones compatibles, état de support réel", 6, 6,
        ImplStatus.IMPLEMENTED, "aucun",
        module="apex.specialties.registry",
        notes="Registre statique honnête : une seule Specialty (« research ») "
              "est déclarée supportée (STUB, comme AgentRuntime), toutes les "
              "autres restent PLANNED — aucune des 15 spécialités du frontend "
              "n'a de moteur backend inventé pour la circonstance. Distinct de "
              "Domain (apprentissage/connaissance, docs/COGNITIVE_ARCHITECTURE.md "
              "§3, toujours PLANNED) : les deux ne sont jamais fusionnés.",
    ),
    # -- Ajouts étape 8 — Scheduler (orchestration multi-missions/clones) -----
    Abstraction(
        "Scheduler",
        "décide QUAND lancer une mission QUEUED — priorité/FIFO, "
        "dépendances entre missions, capacité/backpressure, annulation "
        "réelle, heartbeat, récupération après redémarrage", 8, 11,
        ImplStatus.STUB, "pas de boucle de fond ; capacité non partagée entre instances",
        module="apex.scheduler.service",
        notes="STUB et non IMPLEMENTED délibérément — limitations réelles, "
              "pas un manque d'intégration : depuis l'étape 11, "
              "`POST /missions/{id}/launch` (apex.api.routes.missions) est le "
              "SEUL déclencheur réel de l'application et passe RÉELLEMENT par "
              "ce Scheduler (singleton applicatif paresseux, "
              "apex.api.dependencies.get_scheduler_service — jamais recréé par "
              "requête, jamais hébergé comme runtime par FastAPI) ; "
              "launch_mission() n'est plus exposée comme point d'orchestration "
              "HTTP, seul le Scheduler l'appelle encore. Arbitrage humain "
              "étape 11 : AUCUNE boucle de fond autonome — tick() n'est "
              "déclenché qu'à la demande (par /launch), jamais par un thread "
              "périodique ; une boucle permanente reste PLANNED, pourra être "
              "ajoutée sans modifier ce module. HeartbeatService (existant "
              "depuis l'étape 8) n'est délibérément PAS démarré : "
              "`GET /scheduler` rapporte heartbeat=UNKNOWN, jamais une valeur "
              "fabriquée. N'appelle jamais AgentRuntime/SandboxProvider "
              "directement — uniquement apex.missions.execution.launch_mission "
              "(déjà STUB, étape 7), jamais de contournement ni de duplication "
              "de PolicyEngine. Réclamation QUEUED -> RUNNING atomique en base "
              "(un seul UPDATE conditionnel) testée sous concurrence réelle "
              "(PostgreSQL, plusieurs workers ET plusieurs instances de "
              "SchedulerService, y compris via l'API désormais) : aucune "
              "mission ne peut être lancée deux fois. max_concurrent_missions "
              "borne RÉELLEMENT le pool de threads d'UNE instance (aucune "
              "croissance illimitée) mais n'est PAS un plafond partagé entre "
              "plusieurs instances simultanées — chacune applique sa propre "
              "borne locale, limite documentée, pas résolue ici. Annulation "
              "réelle (os.killpg, vérifiée par constat direct, pas supposée) "
              "testée contre de vraies exécutions sandboxées. RecoveryService "
              "ne reprend JAMAIS une mission interrompue : toute ligne "
              "RUNNING/non-terminale trouvée à la première construction du "
              "singleton dans ce processus est marquée orpheline, jamais "
              "résumée. PAS de retry automatique (reste PLANNED, décision "
              "explicite) et PAS de résolution automatique d'agent (une "
              "mission sans assigned_agent reste QUEUED). Distinct de "
              "Placement (PLANNED, sélection entre plusieurs ComputePool) : ce "
              "Scheduler compose les ComputePool status=ACTIVE déclarés en "
              "configuration mais ne choisit pas encore ENTRE plusieurs pools "
              "réels — un seul nœud existe aujourd'hui, l'abstraction "
              "(apex.scheduler.capacity) ne suppose pas que ça restera le cas.",
    ),
    # -- Ajouts étape 9 — sécurité des données non fiables (architecture et
    # documentation seules, voir docs/UNTRUSTED_DATA_SECURITY.md ; aucun code
    # n'existe pour cette entrée, PLANNED au sens strict) ----------------------
    Abstraction(
        "FileScanner",
        "analyse de sécurité d'un contenu non fiable (fichier, page web, "
        "image, vidéo) avant toute extraction ou promotion en mémoire", 9, 9,
        ImplStatus.PLANNED,
        "aucune analyse — tout contenu externe reste UNTRUSTED indéfiniment "
        "tant qu'aucun scanner n'existe",
        notes="Seul ajout réel du pipeline UNTRUSTED DATA -> QUARANTINE -> "
              "SECURITY ANALYSIS -> RISK ASSESSMENT -> SANDBOX -> EXTRACTION "
              "-> PROVENANCE -> MEMORY documenté dans "
              "docs/UNTRUSTED_DATA_SECURITY.md — chaque autre étage compose "
              "des abstractions déjà existantes (QUARANTINE : `TrustLevel` "
              "déjà existant, apex.config.schema, + `ArtifactStore` PLANNED ; "
              "SANDBOX : `SandboxProvider`, STUB réel depuis l'étape 7 ; "
              "SECRETS : `SecretBroker`, PLANNED ; PROVENANCE/MEMORY : "
              "`MemoryProvider`, STUB depuis l'étape 9). N'exécute jamais de "
              "contenu lui-même — un scan qui a besoin d'exécuter délègue à "
              "`SandboxProvider`, jamais un chemin d'exécution parallèle.",
    ),

    # -- Ajouts passe « APEX — Première interface utilisable » — Chat réel,
    # Conversations, Projects, AgentTemplate. Testé au 4-points avant
    # création : (1) aucun composant existant ne porte déjà ces
    # responsabilités — `apex.missions` gouverne un travail asynchrone
    # soumis à REVIEW humaine (ADR-0011), un tour de Chat est une action
    # humaine directe à réponse immédiate, jamais soumise à cette
    # gouvernance ; (2) les ajouter à `MissionEngine` violerait son contrat
    # (mélanger un cycle de vie synchrone et un cycle de vie asynchrone
    # gouverné) ; (3) ≥2 consommateurs réels dès cette passe (l'UI Chat, et
    # l'API elle-même comme second appelant indépendant des services) ; (4)
    # aucun doublon — `ConversationService.send_message` RÉUTILISE
    # directement `apex.routing.select_model`/`generate_with_fallback`/
    # `apex.models.registry.resolve_candidates`, rien n'est recalculé. -----
    Abstraction(
        "ConversationEngine", "Chat réel — conversations, messages, génération via ModelRouter", 23, 23,
        ImplStatus.STUB,
        "aucun secours conversationnel — un échec de génération revient "
        "comme message d'erreur explicite, jamais un texte inventé",
        module="apex.conversations.service",
        notes="STUB et non IMPLEMENTED délibérément : `create`/`rename`/"
              "`delete`/`list_all`/`list_messages` sont réels (PostgreSQL, "
              "SQLite en mémoire pour les tests). `send_message` est le "
              "chemin réel complet : UI -> API -> `ConversationService."
              "send_message` -> `apex.routing.select_model` -> `apex."
              "routing.generate_with_fallback` -> `apex.models.provider."
              "get_default_provider` -> `OllamaProvider`/"
              "`OpenAICompatibleProvider` -> backend réel -> réponse "
              "réelle, persistée (`MessageRow`). Limites RÉELLES, "
              "documentées plutôt que masquées : (1) pas de streaming — "
              "`generate()` reste bloquant, `generate_stream` "
              "(`OllamaProvider`) n'est pas branché ici ; (2) pas de "
              "mémoire de conversation envoyée au modèle — chaque message "
              "n'envoie QUE son propre contenu comme prompt, jamais "
              "l'historique (un vrai contexte multi-tour exigerait de "
              "composer les messages précédents dans le prompt, non fait "
              "cette passe) ; (3) sélection de modèle sans historique de "
              "qualité/coût/latence — même limite déjà documentée pour "
              "`ModelRouter`, réutilisée telle quelle, jamais dupliquée.",
    ),
    Abstraction(
        "ProjectStore", "regroupement nommé de conversations", 23, 23,
        ImplStatus.STUB,
        "aucun projet — une conversation reste alors non classée (project_id=None)",
        module="apex.projects.service",
        notes="STUB : `create`/`get`/`list_all` réels (PostgreSQL). Aucune "
              "mutation ni suppression dans cette passe — un projet "
              "regroupant déjà des conversations, le supprimer "
              "orphelinerait leur `project_id` (voir `apex.db.repositories."
              "ProjectRepository`, docstring). Volontairement SANS jalons, "
              "kanban ni timeline — `ProjectManagerPage` (frontend) reste "
              "`ui-scaffold` pour ces aspects, seule l'association "
              "conversation <-> projet est réelle.",
    ),
    Abstraction(
        "AgentTemplate", "définition déclarative d'agent (rôle, modèle, outils)", 23, 23,
        ImplStatus.STUB,
        "aucun template — un rôle d'agent doit être choisi manuellement à chaque Mission",
        module="apex.agent_templates.service",
        notes="STUB : `create`/`list_all`/`delete` réels (PostgreSQL). "
              "**Jamais un spawn réel** — distinct par construction "
              "d'`AgentRuntime`/`SPAWN_CLONE` : un `AgentTemplate` "
              "persiste un nom/rôle/modèle préféré/outils déclarés, "
              "toujours `status=DEFINED`, jamais une autre valeur qui "
              "laisserait croire à une exécution. `AgentRole` (CODER/"
              "RESEARCHER/TESTER/ANALYST/DESIGNER/SECURITY/EVALUATOR) est "
              "un vocabulaire délibérément DISTINCT de `Subject`/"
              "`CloneType` (research/learning/developer/...) — faire "
              "tourner un template exigerait un mapping vers un "
              "`CloneType` réel au moment du spawn, non câblé ici. "
              "`tools` reste une liste déclarée, jamais appliquée : "
              "`ToolProvider` est lui-même `PLANNED`.",
    ),

    # -- Ajout passe « Agents V1 » — un Agent réel, exécutable, assignable à
    # une Mission, réutilisant le Model Router déjà réel. Testé au 4-points :
    # (1) aucun composant existant ne porte cette responsabilité —
    # `AgentTemplate` reste un blueprint jamais exécutable, `Clone`/
    # `AgentRuntime` est une instance OS-level spawnée, ni l'un ni l'autre
    # ne configure « quel modèle/quels outils/quelles permissions une
    # exécution utilisera » de façon réutilisable entre plusieurs Missions ;
    # (2) l'ajouter à `MissionEngine` violerait son contrat (Mission gouverne
    # un cycle de vie, pas une configuration réutilisable d'exécutant) ;
    # (3) ≥2 consommateurs réels dès cette passe (`POST /missions/{id}/run`,
    # et l'UI Agents indépendamment de toute Mission particulière) ; (4)
    # aucun doublon — `run_execution` RÉUTILISE directement `apex.routing.
    # select_model`/`generate_with_fallback`/`apex.missions.service.
    # MissionService.mark_running/mark_review/mark_failed`, une troisième
    # composition après `generate_mission_response`/`generate_mission_
    # response_via_router`, jamais une architecture parallèle. ---------------
    Abstraction(
        "AgentRegistry", "Agent réel — configuration réutilisable, exécutable via le Model Router", 24, 24,
        ImplStatus.STUB,
        "aucun agent réutilisable — chaque exécution doit composer candidats/permissions manuellement",
        module="apex.agent_registry.service",
        notes="STUB et non IMPLEMENTED délibérément : `create`/`update`/"
              "`delete`/`list_all`/`instantiate_from_template` sont réels "
              "(PostgreSQL, SQLite en mémoire pour les tests). `run_execution` "
              "est le chemin réel complet : UI -> API (`POST /missions/{id}/"
              "run`) -> `AgentRegistryService.run_execution` -> `apex.routing."
              "select_model` -> `apex.routing.generate_with_fallback` -> "
              "`get_default_provider()` -> provider réel -> réponse réelle, "
              "la Mission traversant son cycle de vie RÉEL (`MissionService."
              "mark_running`/`mark_review`/`mark_failed`, ADR-0011 inchangée). "
              "Permissions (`Permission` — READ_ONLY/WORKSPACE_WRITE/EXECUTE/"
              "NETWORK/GIT_READ/GIT_WRITE) et outils (`ToolName`) JAMAIS "
              "accordés par défaut — un `Agent` créé sans permission "
              "explicite ne peut pas être exécuté (`run_execution` refuse "
              "sans `Permission.EXECUTE`, vérifié réellement). `AgentStatus` "
              "(IDLE/QUEUED/RUNNING/WAITING/COMPLETED/FAILED/DISABLED) n'est "
              "JAMAIS stocké — toujours dérivé (`derive_agent_status`) de "
              "`Agent.enabled` + la dernière `AgentExecution` réelle ; "
              "`WAITING` reste dans le vocabulaire fermé mais n'est produit "
              "par aucune source aujourd'hui. `FallbackReason` (9 valeurs "
              "fermées) : seuls SUCCESS/TIMEOUT/CAPABILITY_MISSING/"
              "PROVIDER_ERROR sont réellement dérivés (`classify_outcome`) — "
              "QUOTA_EXCEEDED/RATE_LIMITED/OFFLINE/AUTH_ERROR/"
              "CONTEXT_TOO_LONG restent déclarés mais jamais produits, faute "
              "de code d'erreur structuré exposé par les providers actuels "
              "(`apex.errors.ModelProviderError` ne porte qu'un message "
              "texte, jamais un code HTTP — deviner depuis ce texte serait "
              "exactement l'erreur que ce dépôt interdit ailleurs). Aucun "
              "champ token/coût/latence sur `AgentExecution` — faute d'être "
              "réellement fourni par `apex.models.model.GenerationResult` "
              "aujourd'hui, jamais inventé. `ToolName.is_operational` est "
              "toujours `False` : `ToolRegistry`/`ToolProvider` restent "
              "`PLANNED` (registre, zéro code) — déclarer un outil sur un "
              "Agent n'en fait jamais un outil réellement exécutable. "
              "`AgentTeam` volontairement minimal (nom/membres) — aucune "
              "communication autonome entre agents, aucun orchestrateur "
              "multi-agent : ce workflow avancé (Planner -> Agent A -> "
              "Agent B -> Agent C -> Evaluator) reste explicitement "
              "`PLANNED`, aucun code ne le prétend implémenté.",
    ),
    Abstraction(
        "Population", "boucle évolutive de clones (Population -> Selection -> "
        "Genome -> Experiment -> Fitness -> nouvelle génération)", 25, 25,
        ImplStatus.STUB, "un seul clone, un seul Genome global, aucune évolution",
        module="apex.metalearner.evolution",
        notes="STUB et non IMPLEMENTED délibérément : `PopulationEvolutionService."
              "evolve_generation` compose RÉELLEMENT `AgentRuntime.spawn`, "
              "`GenomeStoreService.propose` (`mutate`/`crossover`, Phase 17, "
              "jamais réimplémentés), `apex.genome.selection.successive_"
              "halving` (Phase 17) et `apex.metalearner.cycle."
              "run_experiment_cycle` (Phase 5+) — aucune seconde "
              "implémentation d'aucun de ces éléments. `apex.metalearner."
              "population_model.PopulationMember` n'ajoute qu'une table de bookkeeping "
              "(`population_members`) reliant un `Clone` réel (`agents."
              "clone_id`) à une `GenomeVersion` réelle (`genome_versions."
              "id`) — pas de classe `Population` séparée (une population "
              "EST l'ensemble des membres qui partagent `specialty`). "
              "`apex.genome.clone_config.resolve_resource_limits` est la "
              "PREMIÈRE influence réelle du contenu d'un Genome sur "
              "l'exécution d'un clone (`ResourceLimits`, bornée pour ne "
              "jamais dépasser la config administrateur) — tout le reste du "
              "contenu d'un Genome (stratégie/modèle/skills activés) reste "
              "inerte, non câblé. `apex.metalearner.population_fitness.compute_fitness` "
              "est déterministe, sur des métriques RÉELLEMENT mesurées "
              "(`OracleVerdict`, `elapsed_s`) — jamais inventées. Sélection "
              "par `successive_halving` (élite/champion/hibernating/"
              "retired) : des ÉTIQUETTES de bookkeeping interne, JAMAIS une "
              "promotion `PolicyEngine` — `PROMOTE_GENOME_VERSION`/"
              "`PROMOTE_SKILL` restent HUMAIN seul, inchangés, jamais "
              "contournés. Un contrôle HELDOUT réel (`TaskSplit.HELDOUT`) "
              "protège la promotion `CHAMPION` contre le surapprentissage. "
              "Idempotent (`experiment_ref`, colonne `UNIQUE`) et isolé aux "
              "pannes (un membre en échec ne bloque jamais la génération). "
              "Séquentiel, jamais concurrent (§16, une future "
              "parallélisation resterait un pas séparé). Plafonné "
              "(`PopulationPolicy.max_population_size`/`generation_size`), "
              "valeurs prudentes par défaut. Ce qui reste PLANNED : "
              "`crossover` n'est utilisé qu'entre deux élites déjà "
              "existantes (jamais à la génération 0) ; aucun orchestrateur "
              "multi-génération automatique (l'appelant décide de "
              "déclencher chaque génération) ; le contenu d'un Genome "
              "n'influence toujours PAS le choix de modèle/stratégie/skills "
              "activés (uniquement les limites de ressources) ; aucune "
              "évolution distribuée, aucun ordonnancement GPU avancé, aucun "
              "front de Pareto multi-objectif (réutilisable — `apex.genome."
              "selection.pareto_front` existe déjà — mais non branché ici).",
    ),
    Abstraction(
        "GauntletEvaluation", "évaluation adversariale stricte, multi-juges, avant "
        "promotion CHAMPION (Gauntlet -> Juges spécialisés -> Agrégation déterministe "
        "-> PASS/REJECT/INCONCLUSIVE)", 26, 26,
        ImplStatus.STUB, "6 dimensions réelles (CORRECTNESS/ROBUSTNESS/EFFICIENCY/"
        "SECURITY/GENERALIZATION/REGRESSION) ; EDGE_CASES/QUALITY/ADVERSARIAL PLANNED",
        module="apex.metalearner.gauntlet",
        notes="STUB et non IMPLEMENTED délibérément : `apex.metalearner.judges` "
              "n'instancie que 6 des 9 dimensions nommées par `JudgeDimension` — "
              "chacune ENVELOPPE une preuve déjà réelle (`OracleVerdict`, "
              "`ExecutionStatus`, `elapsed_s`, `fitness_components` persistés du "
              "champion), aucune n'invente de mesure. `EDGE_CASES`/`QUALITY`/"
              "`ADVERSARIAL` restent PLANNED — aucun juge n'y est jamais associé, "
              "même discipline que `JudgeTier.TIER_1`/`TIER_2` (Phase 9). Composé "
              "au-dessus de `PopulationEvolutionService` (N+6), JAMAIS un second "
              "pipeline concurrent : le gauntlet `cheap` (CORRECTNESS/EFFICIENCY/"
              "SECURITY/REGRESSION) est calculé pour CHAQUE membre sans aucune "
              "exécution supplémentaire ; le gauntlet `full` (ajoute ROBUSTNESS — "
              "ré-exécution réelle du même couple tâche/Genome — et GENERALIZATION "
              "— enveloppe le contrôle HELDOUT déjà réel de N+6) ne s'exécute QUE "
              "pour le candidat à la promotion CHAMPION, remplaçant l'ancien "
              "contrôle HELDOUT booléen seul par une décision agrégée. Véto "
              "déterministe : un `JudgeFinding` `CRITICAL` bloque la promotion quel "
              "que soit le score des autres dimensions, jamais moyenné — persisté "
              "(`gauntlet_results`, INSERT seul) avec `judge_id`/`judge_version`/ "
              "`protocol_version` (`GAUNTLET_PROTOCOL_VERSION`) pour rester "
              "interprétable après une future évolution du protocole. Un juge qui "
              "lève devient `INCONCLUSIVE`, jamais un `PASS` silencieux — "
              "l'agrégation est une fonction PURE, déterministe, indépendante de "
              "l'ordre des juges. Ce qui reste PLANNED : `EDGE_CASES` (aucun mode "
              "de génération de tâches dégénérées dédié), `QUALITY` (chevaucherait "
              "`CORRECTNESS` sans preuve supplémentaire distincte), `ADVERSARIAL` "
              "(perturbation/mutation-based tests, distinct du contrôle HELDOUT déjà "
              "réel sous `GENERALIZATION`) ; aucun juge LLM ; aucune calibration de "
              "juge (faux positifs/négatifs, réputation) — seul le modèle de "
              "données le permet déjà ; aucun raccordement au Weakness/Curriculum "
              "Engine au-delà de l'événement `evolution.gauntlet_rejected_"
              "champion_candidacy` (`EventRepository`, observabilité humaine — "
              "pas encore un signal d'apprentissage consommé automatiquement).",
    ),
    # -- Ajout N+16 — Operator (couche de contrôle centrale d'APEX) -----------
    Abstraction(
        "Operator",
        "contrôle du lifecycle d'APEX (Start/Stop/Pause/Resume/Status) et façade "
        "unique de contrôle des tâches (Submit/Cancel/GetStatus/GetResult/"
        "GetRecentOperations), consommable par une future UI sans terminal", 27, 27,
        ImplStatus.STUB, "pas de boucle de fond partagée entre plusieurs instances ; "
        "pas de mapping appelant -> Subject précis (même limite que /missions)",
        module="apex.operator.service",
        notes="STUB et non IMPLEMENTED délibérément, mêmes raisons structurelles que "
              "`Scheduler` : `OperatorService` (singleton applicatif, "
              "apex.api.dependencies.get_operator_service) ajoute la boucle de "
              "supervision permanente que l'entrée « Scheduler » ci-dessus documentait "
              "explicitement comme PLANNED (« pourra être ajoutée sans modifier ce "
              "module ») — SchedulerService.tick()/cancel_mission()/capacity() restent "
              "entièrement inchangés, jamais dupliqués. Un « Task » au sens Operator "
              "EST une apex.missions.model.Mission — jamais un second concept ni un "
              "second moteur d'exécution : SubmitTask délègue à MissionService.create "
              "puis tente un ordonnancement immédiat (scheduler.tick(), sous un verrou "
              "dédié — voir _tick_lock — pour ne jamais laisser le thread de "
              "supervision et un appel explicite s'entrelacer) ; CancelTask délègue "
              "entièrement à SchedulerService.cancel_mission (annulation réelle, "
              "killpg vérifié) ; GetTaskStatus/GetTaskResult projettent directement "
              "l'état/le résultat persistés de la Mission ; GetRecentOperations "
              "projette apex.db.repositories.EventRepository.recent (aucun second "
              "journal). Machine à états fermée (apex.operator.states, même patron que "
              "apex.missions.states) : STOPPED/STARTING/READY/RUNNING/PAUSED/"
              "STOPPING/FAULTED, READY<->RUNNING dérivés automatiquement de "
              "ExecutionRegistry, jamais une décision de l'appelant. PAUSED n'interrompt "
              "JAMAIS une tâche déjà en cours (ce serait une annulation déguisée) — "
              "suspend seulement le déclenchement de nouvelles tâches. Récupération "
              "après erreur : un `start()` en échec (Scheduler/DB indisponible) "
              "transitionne vers FAULTED avec l'erreur observable via `status()` "
              "(jamais un READY optimiste) ; un `start()` ultérieur, une fois le "
              "Runtime de nouveau sain, ramène à READY normalement. Autorisation : "
              "START_OPERATOR/STOP_OPERATOR/PAUSE_OPERATOR/RESUME_OPERATOR "
              "(apex.policy.model.Action, N+16) réservées à Subject.HUMAN seul (ni "
              "CORE, ni aucun clone) ; SubmitTask/CancelTask n'introduisent AUCUNE "
              "action nouvelle, l'autorisation reste entièrement celle déjà déléguée à "
              "MissionService/SchedulerService (CREATE_MISSION/CANCEL_MISSION), jamais "
              "dupliquée ici. Exposé via 10 routes HTTP (`/api/v1/operator/*`, même "
              "authentification par jeton et même hypothèse simplificatrice "
              "Subject.HUMAN implicite que `/missions`) — voir apex/api/routes/"
              "operator.py. Limite honnête, même nature que celle de `Scheduler` : "
              "aucun état de supervision partagé entre plusieurs instances/processus "
              "(le thread de supervision est local à CE processus, comme "
              "ExecutionRegistry) ; aucune persistance du lifecycle Operator lui-même "
              "(redémarrer le processus API repart de STOPPED, jamais un état fantôme) "
              "— un choix délibéré, pas un oubli : l'état d'installation/Runtime/Gate "
              "réel reste entièrement celui déjà persisté par Setup (voir "
              "setup/docs/LAUNCHER_ARCHITECTURE.md, N+15), jamais réinventé ici.",
    ),
    Abstraction(
        "DesignEngine", "pipeline de conception assistée (brief -> concepts -> visuels -> paquet)", 28, 28,
        ImplStatus.STUB, "recherche et génération visuelle indisponibles — pipeline structurel réel",
        module="apex.design.service",
        notes="STUB et non IMPLEMENTED délibérément (N+17) : les 7 étapes "
              "(ANALYZE/RESEARCH/SYNTHESIZE/DESIGN/VISUALIZE/VALIDATE/PACKAGE, "
              "`apex.design.states.DesignPipelineState`) s'exécutent RÉELLEMENT — "
              "machine à états fermée (même patron que `apex.missions.states`), "
              "chaque étape produit un `StepRecord` (durée/provenance/logs "
              "réels) et un artefact adressé par contenu RÉELLEMENT écrit "
              "dans `apex.artifacts` (première utilisation réelle de ce "
              "magasin, jusqu'ici non câblé — voir son entrée `ArtifactStore` "
              "ci-dessus). ANALYZE/SYNTHESIZE/DESIGN (`apex.design.synthesis`) "
              "sont déterministes, jamais un appel `ModelProvider` simulé — "
              "extension future documentée, pas une simulation aujourd'hui. "
              "RESEARCH (`apex.design.research.ResearchProvider`) retourne "
              "honnêtement zéro résultat : aucun backend de recherche web "
              "réel n'est connecté dans ce dépôt (même vide que `SearchProvider` "
              "ci-dessus, PLANNED depuis la Phase 13 — non comblé par N+17, "
              "seulement consommé honnêtement). VISUALIZE consomme "
              "`VisualGenerationProvider` (entrée séparée ci-dessous) — un "
              "prompt structuré RÉEL est toujours construit "
              "(`apex.design.prompt_engine`), le résultat est marqué "
              "`VisualResult.state=\"unavailable\"` (jamais fabriqué) en "
              "l'absence de backend. VALIDATE (`apex.design.validation`) est "
              "une QA structurelle réelle (présence/cohérence/provenance/ordre "
              "des étapes), jamais une validation par modèle de vision "
              "(aucun modèle de vision connecté — architecture prête, non "
              "simulée). Reprise après échec (`DesignPipeline.resume`) réelle : "
              "repart de la dernière étape en échec, jamais une ré-exécution "
              "complète. Intégration Operator (N+16) : `OperatorService."
              "submit_design`/`resume_design`/`get_design_status`/"
              "`get_design_result`, même garde `accepts_submissions` que "
              "`submit_task`, même journal `events` — jamais un second canal. "
              "Autorisation : une seule action nouvelle, `SUBMIT_DESIGN` "
              "(`apex.policy.model.Action`), réservée à `Subject.HUMAN` seul, "
              "même motif que `CREATE_MISSION`. Intégration Memory/RAG "
              "délibérément ABSENTE en v1 (décision explicite, pas un oubli) : "
              "câbler une écriture `MemoryService.write()` depuis le Design "
              "Engine exigerait d'étendre l'allow-list HUMAN/CORE avec "
              "`WRITE_OWN_MEMORY` — un changement de portée de sécurité "
              "hors du périmètre de cette étape, jamais fait sans décision "
              "explicite. Decision Trace : ABSENTE de ce dépôt (aucune "
              "occurrence, aucune entrée de registre) — rien à intégrer, "
              "constaté par audit N+17, pas une omission.",
    ),
    Abstraction(
        "VisualGenerationProvider", "génération d'image agnostique du backend pour le Design Engine", 28, 28,
        ImplStatus.STUB, "aucun backend réel connecté — VISUAL_BACKEND_UNAVAILABLE explicite",
        module="apex.design.visual_provider",
        notes="Contrat ABC réel (`generate`/`status`/`retrieve`/`validate`), "
              "même patron qu'`ArtifactStoreProvider`/`ModelProvider` : un "
              "seul point de construction (`get_default_provider()`). "
              "`NullVisualGenerationProvider` est le comportement de "
              "PRODUCTION actuel (pas un double de test) : `generate()` lève "
              "`apex.errors.VisualBackendUnavailable` plutôt que de "
              "prétendre lancer une génération — confirmé par audit N+17 : "
              "aucune dépendance de génération d'image, aucune clé API dans "
              "ce dépôt, `apex.routing.model.Capability.VISION` jamais "
              "dérivée. Un double de test (`_MockVisualGenerationProvider`) "
              "existe UNIQUEMENT dans `tests/test_design_pipeline.py`, jamais "
              "dans ce module — même discipline que `_FakeProvider("
              "ModelProvider)` dans `tests/test_routing_generation.py`.",
    ),
)



def by_name(name: str) -> Abstraction:
    for item in ABSTRACTIONS:
        if item.name == name:
            return item
    raise KeyError(f"abstraction inconnue : {name}")


def module_importable(abstraction: Abstraction) -> bool:
    """Vérifie qu'un module déclaré est réellement importable."""
    if not abstraction.module:
        return False
    try:
        importlib.import_module(abstraction.module)
    except ImportError:
        return False
    return True


def summary() -> dict[str, int]:
    counts = {status.value: 0 for status in ImplStatus}
    for item in ABSTRACTIONS:
        counts[item.status.value] += 1
    return counts


def available_at_phase(phase: int) -> tuple[Abstraction, ...]:
    """Abstractions dont l'implémentation est attendue au plus tard à `phase`."""
    return tuple(a for a in ABSTRACTIONS if a.phase_implemented <= phase)
