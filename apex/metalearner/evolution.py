"""`PopulationEvolutionService` — la première boucle évolutive RÉELLE d'APEX
(passe « Real Clone Population + Evolution Loop ») :

    Population -> Selection -> Clone/Fork -> Genome application
        -> Experiment assignment -> Execution -> Evaluation -> Fitness
        -> Selection -> Skill/Genome feedback -> Mutation -> nouvelle génération

Même patron de composition qu'`apex.metalearner.cycle.run_experiment_cycle`
et `apex.metalearner.autonomy.AutonomousLearningService` : une classe fine
qui COMPOSE des primitives déjà réelles, n'en réimplémente aucune —

    `AgentRuntime.spawn`               (Phase 8, inchangé)
    `GenomeStoreService.propose`       (Phase 17, inchangé)
    `apex.genome.mutation.mutate/crossover` (Phase 17, inchangé)
    `apex.genome.selection.successive_halving` (Phase 17, inchangé)
    `apex.metalearner.cycle.run_experiment_cycle` (Phase 5+, inchangé)
    `apex.metalearner.feedback.LearningFeedbackService` (N+5, inchangé)
    `apex.metalearner.population_service.PopulationService` (cette passe,
        bookkeeping seul — voir sa propre docstring)

Vit dans `apex.metalearner` (comme `apex.population_model`/
`population_repository`/`population_service`/`population_fitness`
ci-contre) plutôt que dans un nouveau paquet `apex.evolution`/
`apex.population` séparé : `apex/` a une partition figée Runtime/Research
(`runtime_build/selection.py`, fichier RÉSERVÉ de la passe Launcher, jamais
modifié ici) qui exige que TOUT sous-paquet de premier niveau y soit classé
— en ajouter un nouveau casserait cette garde sans toucher un fichier
interdit. `apex.metalearner` est déjà classé Research et est le foyer
naturel de cette boucle (elle EST la suite de `apex.metalearner.cycle`/
`autonomy`), donc aucun nouveau sous-paquet n'est créé.

**Déclenchement HUMAIN, exactement le modèle N+4** : `evolve_generation` ne
prend AUCUN paramètre d'identité — comme `AutonomousLearningService.
start_cycle`, `Subject.HUMAN` est fourni en dur à `run_experiment_cycle`
(exigé par `TaskBankService.create`), jamais négociable depuis l'extérieur.
Les clones sont spawnés `Subject.LEARNING`/`spawned_by=Subject.CORE`, les
Genomes proposés `created_by=Subject.CORE` — jamais promus automatiquement
(`PROMOTE_GENOME_VERSION` reste HUMAIN seul, inchangé).

**Champion/élite sont des étiquettes de bookkeeping, jamais une promotion**
— voir la docstring d'`apex.metalearner.population_service`. Le déblocage
HELDOUT (§13) est la seule barrière avant qu'un candidat devienne
`CHAMPION` : un candidat qui échoue ce contrôle reste `ELITE`, jamais promu
au rang de champion, jamais un candidat rejeté pour autant (aucune perte
de données).

**Séquentiel, jamais concurrent** (§16) : chaque membre d'une génération
est traité l'un après l'autre — la même discipline qu'`AutonomousLearningService`
impose déjà UN cycle actif à la fois par processus. Une future parallélisation
resterait un pas séparé, pas celui-ci.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apex.agents.runtime import AgentRuntime
from apex.errors import PopulationCapExceeded
from apex.evalrunner.model import EvalMethod
from apex.genome.model import GenomeVersionOrigin
from apex.genome.mutation import crossover, mutate
from apex.genome.selection import successive_halving
from apex.genome.service import GenomeStoreService
from apex.metalearner.cycle import run_experiment_cycle
from apex.metalearner.population_fitness import compute_fitness
from apex.metalearner.population_model import MemberStatus, PopulationMember
from apex.metalearner.population_service import PopulationService
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit

_EVENT_LOGGER = "apex.metalearner.evolution"
_EXPERIMENT_REF_PREFIX = "evolution"
_DEFAULT_MUTATION_RATE = 0.15
_DEFAULT_MUTATION_MAGNITUDE = 0.15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _deterministic_seed(experiment_ref: str) -> int:
    """Même mécanisme qu'`apex.metalearner.feedback._deterministic_seed` —
    rejouer le même `experiment_ref` produit la même mutation/le même
    croisement, jamais un résultat différent à chaque relecture."""
    digest = hashlib.sha256(experiment_ref.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


@dataclass(frozen=True)
class MemberFailure:
    experiment_ref: str
    reason: str


@dataclass(frozen=True)
class EvolutionGenerationResult:
    specialty: str
    generation: int
    members: tuple[PopulationMember, ...]
    champion: PopulationMember | None
    elite: tuple[PopulationMember, ...]
    failures: tuple[MemberFailure, ...] = field(default_factory=tuple)


class PopulationEvolutionService:
    def __init__(
        self,
        session_factory: Any,
        *,
        agent_runtime: AgentRuntime,
        config: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._agent_runtime = agent_runtime
        self._config = config
        self._population = PopulationService(session_factory)
        self._genome = GenomeStoreService(session_factory)

    # -- orchestrateur principal ----------------------------------------------

    def evolve_generation(
        self,
        specialty: str,
        *,
        current_level: int = 1,
        method: EvalMethod = EvalMethod.PRACTICE,
    ) -> EvolutionGenerationResult:
        """UNE génération complète : sélectionne les parents (élite/champion
        de la génération précédente, ou le Genome PROMOTED courant à la
        génération 0), crée jusqu'à `generation_size` nouveaux membres par
        mutation/croisement, les fait expérimenter, mesure leur fitness,
        applique la sélection. Ne boucle PAS elle-même (même choix
        qu'`apex.metalearner.cycle.run_experiment_cycle` : l'appelant décide
        s'il relance une génération suivante)."""
        from apex.config.loader import load_config

        config = self._config or load_config()
        policy = config.population

        generation = self._population.latest_generation(specialty) + 1
        parents = self._select_parents(specialty, generation)

        created: list[PopulationMember] = []
        failures: list[MemberFailure] = []
        for index in range(policy.generation_size):
            experiment_ref = f"{_EXPERIMENT_REF_PREFIX}:{specialty}:gen{generation}:member{index}"
            try:
                member = self._create_member(
                    specialty=specialty, generation=generation, index=index, parents=parents,
                    experiment_ref=experiment_ref, current_level=current_level, method=method,
                    policy=policy,
                )
            except PopulationCapExceeded as exc:
                self._record_event(
                    "evolution.population_cap_reached",
                    {"specialty": specialty, "generation": generation, "reason": str(exc)},
                )
                break
            except Exception as exc:  # §18 : un échec individuel n'arrête jamais la génération
                self._record_event(
                    "evolution.member_failed",
                    {
                        "specialty": specialty, "generation": generation, "experiment_ref": experiment_ref,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    },
                )
                failures.append(MemberFailure(experiment_ref=experiment_ref, reason=str(exc)))
                continue
            if member is not None:
                created.append(member)

        elite, champion = self._select_and_promote(specialty, generation, created, policy)
        # `created` est réinterrogé APRÈS la sélection : chaque membre y
        # portait encore son statut `EXPERIMENTAL` d'origine (capturé au
        # moment de `record_fitness`, avant toute décision) — le rapport
        # final doit refléter l'état RÉEL et définitif de chaque membre.
        final_members = [self._population.get(m.id) or m for m in created]
        return EvolutionGenerationResult(
            specialty=specialty, generation=generation, members=tuple(final_members),
            champion=champion, elite=tuple(elite), failures=tuple(failures),
        )

    # -- création d'UN membre --------------------------------------------------

    def _create_member(
        self,
        *,
        specialty: str,
        generation: int,
        index: int,
        parents: tuple[PopulationMember | None, PopulationMember | None],
        experiment_ref: str,
        current_level: int,
        method: EvalMethod,
        policy: Any,
    ) -> PopulationMember | None:
        existing = self._population.get_by_experiment_ref(experiment_ref)
        if existing is not None:
            return existing  # idempotence (§17) — replay sûr, jamais un doublon.

        parent_a, parent_b = parents
        genome_version, genome_content, parent_member_id = self._propose_offspring_genome(
            specialty=specialty, parent_a=parent_a, parent_b=parent_b,
            experiment_ref=experiment_ref, use_crossover=policy.enable_crossover and index % 2 == 1,
        )

        from apex.agents.pool_selection import pick_sandbox_capable_pool
        from apex.config.loader import load_config

        config = self._config or load_config()
        pool = pick_sandbox_capable_pool(config)
        clone = self._agent_runtime.spawn(
            Subject.LEARNING, pool=pool, genome_ref=genome_version.content_hash, spawned_by=Subject.CORE,
        )

        # Plafond vérifié APRÈS l'idempotence (un replay ne doit jamais être
        # bloqué par un plafond depuis atteint) mais AVANT la création
        # persistée du membre — la même discipline que `PopulationService.
        # add_member`, qui applique déjà ce contrôle ; il est répété ici
        # explicitement pour ne dépenser le coût réel d'une expérimentation
        # (spawn + sandbox + oracle) que si la population a effectivement de
        # la place.
        active = self._population.count_active(specialty)
        if active >= policy.max_population_size:
            raise PopulationCapExceeded(specialty, active=active, limit=policy.max_population_size)

        result = run_experiment_cycle(
            session_factory=self._session_factory, agent_runtime=self._agent_runtime,
            clone_id=clone.clone_id, specialty=specialty, current_level=current_level,
            requested_by=Subject.HUMAN, method=method, split=TaskSplit.TRAIN,
            experiment_ref=experiment_ref, genome_content=genome_content,
        )

        skill_candidate_id, genome_candidate_id = self._process_feedback(result, experiment_ref)

        member = self._population.add_member(
            specialty=specialty, generation=generation, clone_id=clone.clone_id,
            genome_version_id=genome_version.id, parent_member_id=parent_member_id,
            created_by=Subject.CORE.value, experiment_ref=experiment_ref,
            max_population_size=policy.max_population_size,
        )

        limits = self._resolved_limits(genome_content)
        fitness = compute_fitness(result.eval_run, limits=limits)
        return self._population.record_fitness(
            member.id, fitness, skill_candidate_id=skill_candidate_id, genome_candidate_id=genome_candidate_id,
        )

    def _propose_offspring_genome(
        self,
        *,
        specialty: str,
        parent_a: PopulationMember | None,
        parent_b: PopulationMember | None,
        experiment_ref: str,
        use_crossover: bool,
    ) -> tuple[Any, dict[str, Any], str | None]:
        seed = _deterministic_seed(experiment_ref)
        if parent_a is None:
            # Génération 0 (bootstrap) : mute le Genome PROMOTED courant —
            # jamais une valeur fabriquée si aucun n'existe.
            current = self._genome.get_current()
            if current is None:
                raise ValueError(
                    "aucune GenomeVersion PROMOTED — impossible d'amorcer une population "
                    "(voir apex.genome.service.GenomeStoreService.promote)"
                )
            content = self._genome.get_content(current.id)
            mutated = mutate(content, rate=_DEFAULT_MUTATION_RATE, magnitude=_DEFAULT_MUTATION_MAGNITUDE, seed=seed)
            version = self._genome.propose(
                content=mutated, created_by=Subject.CORE, origin=GenomeVersionOrigin.MUTATION,
                parent_version_id=current.id,
            )
            return version, mutated, None

        content_a = self._genome.get_content(parent_a.genome_version_id)
        if use_crossover and parent_b is not None:
            content_b = self._genome.get_content(parent_b.genome_version_id)
            child_content = crossover(content_a, content_b, seed=seed)
            version = self._genome.propose(
                content=child_content, created_by=Subject.CORE, origin=GenomeVersionOrigin.CROSSOVER,
                parent_version_id=parent_a.genome_version_id, parent_version_id_b=parent_b.genome_version_id,
            )
            return version, child_content, parent_a.id

        child_content = mutate(content_a, rate=_DEFAULT_MUTATION_RATE, magnitude=_DEFAULT_MUTATION_MAGNITUDE, seed=seed)
        version = self._genome.propose(
            content=child_content, created_by=Subject.CORE, origin=GenomeVersionOrigin.MUTATION,
            parent_version_id=parent_a.genome_version_id,
        )
        return version, child_content, parent_a.id

    # -- sélection de parents / de survivants ----------------------------------

    def _select_parents(
        self, specialty: str, generation: int,
    ) -> tuple[PopulationMember | None, PopulationMember | None]:
        """`(None, None)` à la génération 0 (amorce depuis le Genome PROMOTED
        courant) ; sinon jusqu'à deux parents distincts parmi les élites +
        le champion de la génération précédente — jamais une troisième
        source de vérité (aucune moyenne/synthèse de plusieurs Genomes hors
        `crossover`, qui les combine explicitement)."""
        if generation == 0:
            return None, None
        pool = self._population.list_elite(specialty)
        champion = self._population.get_champion(specialty)
        if champion is not None and champion.id not in {m.id for m in pool}:
            pool = [champion, *pool]
        if not pool:
            return None, None
        if len(pool) == 1:
            return pool[0], None
        return pool[0], pool[1]

    def _select_and_promote(
        self, specialty: str, generation: int, members: list[PopulationMember], policy: Any,
    ) -> tuple[list[PopulationMember], PopulationMember | None]:
        if not members:
            return [], self._population.get_champion(specialty)

        scores = {m.id: (m.fitness if m.fitness is not None else float("-inf")) for m in members}
        keep_fraction = min(1.0, policy.elite_size / policy.generation_size)
        survivor_ids = set(successive_halving(scores, keep_fraction=keep_fraction))

        elite: list[PopulationMember] = []
        for member in members:
            if member.id in survivor_ids:
                updated = self._population.set_status(member.id, MemberStatus.ELITE)
                elite.append(updated)
            else:
                # §11 : un échec d'infrastructure (ERROR/TIMEOUT) reste
                # observable (`HIBERNATING`) plutôt que noyé dans `RETIRED`
                # (simple non-sélection sur la performance) — mais dans les
                # deux cas, jamais supprimé.
                components = member.fitness_components or {}
                status = MemberStatus.HIBERNATING if components.get("error_penalty") else MemberStatus.RETIRED
                self._population.set_status(member.id, status)

        best_candidate = max(elite, key=lambda m: m.fitness if m.fitness is not None else float("-inf"), default=None)
        champion = self._promote_champion_if_earned(specialty, generation, best_candidate, policy)
        # Un candidat promu champion sort du pool `elite` (il porte déjà son
        # propre statut) — un candidat NON promu (pas meilleur / échec du
        # contrôle HELDOUT) reste dans `elite` tel quel, jamais perdu.
        if champion is not None and best_candidate is not None and champion.id == best_candidate.id:
            elite = [m for m in elite if m.id != champion.id]
        return elite, champion

    def _promote_champion_if_earned(
        self, specialty: str, generation: int, candidate: PopulationMember | None, policy: Any,
    ) -> PopulationMember | None:
        current_champion = self._population.get_champion(specialty)
        if candidate is None:
            return current_champion
        if current_champion is not None and (candidate.fitness or float("-inf")) <= (current_champion.fitness or float("-inf")):
            return current_champion  # §10 : jamais remplacé par un individu moins performant.

        if not self._holdout_check_passes(specialty, candidate, generation):
            self._record_event(
                "evolution.holdout_gate_failed",
                {"specialty": specialty, "generation": generation, "candidate_id": candidate.id},
            )
            return current_champion  # reste ELITE, jamais promu — aucune perte de données.

        return self._population.set_status(candidate.id, MemberStatus.CHAMPION)

    def _holdout_check_passes(self, specialty: str, candidate: PopulationMember, generation: int) -> bool:
        """§13 : un contrôle HELDOUT réel avant toute promotion CHAMPION —
        empêche un candidat de gagner par simple surapprentissage sur les
        tâches TRAIN utilisées pendant l'évolution. Un clone étant à usage
        unique (`AgentRuntime.execute_in_sandbox` le termine après un seul
        appel), ce contrôle spawn un clone FRAIS avec le MÊME Genome —
        jamais une réutilisation d'un clone déjà terminé."""
        from apex.agents.pool_selection import pick_sandbox_capable_pool
        from apex.config.loader import load_config
        from apex.oracles.model import OracleVerdict

        config = self._config or load_config()
        pool = pick_sandbox_capable_pool(config)
        genome_version = self._genome.get(candidate.genome_version_id)
        if genome_version is None:
            return False
        genome_content = self._genome.get_content(candidate.genome_version_id)
        clone = self._agent_runtime.spawn(
            Subject.LEARNING, pool=pool, genome_ref=genome_version.content_hash, spawned_by=Subject.CORE,
        )
        experiment_ref = f"{_EXPERIMENT_REF_PREFIX}:{specialty}:gen{generation}:member-holdout:{candidate.id}"
        result = run_experiment_cycle(
            session_factory=self._session_factory, agent_runtime=self._agent_runtime,
            clone_id=clone.clone_id, specialty=specialty, current_level=1,
            requested_by=Subject.HUMAN, method=EvalMethod.PRACTICE, split=TaskSplit.HELDOUT,
            experiment_ref=experiment_ref, genome_content=genome_content,
        )
        return result.eval_run.oracle_verdict is OracleVerdict.PASS

    # -- helpers internes -------------------------------------------------------

    def _process_feedback(self, result: Any, experiment_ref: str) -> tuple[str | None, str | None]:
        """Même patron d'isolation qu'`AutonomousLearningService.
        _process_feedback` — jamais susceptible de faire échouer la création
        d'un membre dont l'expérience elle-même a réussi."""
        from apex.metalearner.feedback import LearningFeedbackService

        try:
            outcome = LearningFeedbackService(self._session_factory).process_cycle_result(
                result, experiment_ref=experiment_ref,
            )
        except Exception as exc:
            self._record_event(
                "evolution.feedback_failed",
                {"experiment_ref": experiment_ref, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None, None
        return outcome.skill_candidate_id, outcome.genome_candidate_id

    def _resolved_limits(self, genome_content: dict[str, Any] | None) -> Any:
        """Reproduit EXACTEMENT ce que `EvalRunnerService._default_limits()`
        vient d'appliquer à `result.eval_run` (config sandbox RÉELLE,
        `apex.config.loader.load_config()` — `EvalRunnerService` n'accepte
        aucune config injectée, donc `self._config` ne doit jamais être
        substitué ici, sous peine de calculer un `cost_penalty` incohérent
        avec les limites RÉELLEMENT appliquées à cette exécution)."""
        from apex.config.loader import load_config
        from apex.genome.clone_config import resolve_resource_limits
        from apex.sandbox.model import ResourceLimits

        base = ResourceLimits.from_config(load_config().sandbox)
        return resolve_resource_limits(genome_content, base)

    def _record_event(self, msg: str, context: dict[str, Any]) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import EventRepository

        with session_scope(self._session_factory) as session:
            EventRepository(session).add(ts=_utcnow(), level="INFO", logger=_EVENT_LOGGER, msg=msg, context=context)
