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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from apex.agents.runtime import AgentRuntime
from apex.errors import PopulationCapExceeded
from apex.evalrunner.model import EvalMethod, EvalRun
from apex.genome.model import GenomeVersionOrigin
from apex.genome.mutation import crossover, mutate
from apex.genome.selection import successive_halving
from apex.genome.service import GenomeStoreService
from apex.metalearner.cycle import run_experiment_cycle
from apex.metalearner.gauntlet import GauntletRunner
from apex.metalearner.gauntlet_repository import GauntletRepository
from apex.metalearner.judge_model import GauntletResult, JudgeDecision
from apex.metalearner.judges import JudgeInput
from apex.metalearner.population_fitness import FitnessResult, compute_fitness
from apex.metalearner.population_model import MemberStatus, PopulationMember
from apex.metalearner.population_service import PopulationService
from apex.policy.model import Subject
from apex.tasks.model import TaskSplit

if TYPE_CHECKING:
    from apex.metalearner.judge_feedback_model import EvolutionContext

_EVENT_LOGGER = "apex.metalearner.evolution"
_EXPERIMENT_REF_PREFIX = "evolution"
_DEFAULT_MUTATION_RATE = 0.15
_DEFAULT_MUTATION_MAGNITUDE = 0.15


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _deterministic_seed(experiment_ref: str) -> int:
    """Même mécanisme qu'`apex.metalearner.feedback._deterministic_seed` —
    rejouer le même `experiment_ref` produit la même mutation/le même
    croisement, jamais un résultat différent à chaque relecture."""
    digest = hashlib.sha256(experiment_ref.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _extract_eval_run_id(cheap_result: GauntletResult) -> str | None:
    """Récupère l'`EvalRun.id` référencé par les preuves CITABLES du
    gauntlet CHEAP déjà persisté (`JudgeEvidence(kind="eval_run", ...)`,
    `CorrectnessJudge`/`EfficiencyJudge`/`SecurityJudge`) — jamais une
    nouvelle colonne dédiée sur `PopulationMemberRow`/`GauntletResultRow` :
    la preuve existe déjà, ce module la relit plutôt que de la dupliquer."""
    for judge_result in cheap_result.judge_results:
        for evidence in judge_result.evidence:
            if evidence.kind == "eval_run":
                return evidence.reference
    return None


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
        self._gauntlet_repo = GauntletRepository(session_factory)

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
        # Contexte d'évolution (§8 de la mission N+9) : preuve RÉELLE déjà
        # persistée (`judge_learning_signals`/`gauntlet_results`, N+8/N+9),
        # jamais un second moteur d'évolution — n'influence QUE le biais de
        # mutation (`focus_genome_paths`), jamais la sélection/HELDOUT/véto,
        # inchangés.
        evolution_context = self._build_evolution_context(specialty)

        created: list[PopulationMember] = []
        failures: list[MemberFailure] = []
        for index in range(policy.generation_size):
            experiment_ref = f"{_EXPERIMENT_REF_PREFIX}:{specialty}:gen{generation}:member{index}"
            try:
                member = self._create_member(
                    specialty=specialty, generation=generation, index=index, parents=parents,
                    experiment_ref=experiment_ref, current_level=current_level, method=method,
                    policy=policy, focus_genome_paths=frozenset(evolution_context.focus_genome_paths),
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

        # Curriculum Challenge + Bug Farm (passe N+10, §2/§7) — dérivés
        # UNIQUEMENT de l'état déjà réel (`CurriculumTarget`/
        # `GauntletWeakness`, N+9, inchangés) ; isolés comme le reste de la
        # boucle (§17/§18) : une panne ici ne doit JAMAIS invalider une
        # génération dont les membres ont déjà réellement expérimenté.
        self._sync_challenges_and_bugs(specialty, generation)

        return EvolutionGenerationResult(
            specialty=specialty, generation=generation, members=tuple(final_members),
            champion=champion, elite=tuple(elite), failures=tuple(failures),
        )

    def _sync_challenges_and_bugs(self, specialty: str, generation: int) -> None:
        try:
            from apex.metalearner.challenge_service import CurriculumChallengeService

            CurriculumChallengeService(self._session_factory).build_challenges(specialty, generation)
        except Exception as exc:
            self._record_event(
                "evolution.curriculum_challenge_sync_failed",
                {
                    "specialty": specialty, "generation": generation,
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
            )
        try:
            from apex.metalearner.bug_farm_service import BugFarmService

            BugFarmService(self._session_factory).sync_from_weakness_history(specialty, generation)
        except Exception as exc:
            self._record_event(
                "evolution.bug_farm_sync_failed",
                {
                    "specialty": specialty, "generation": generation,
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
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
        focus_genome_paths: frozenset[str] = frozenset(),
    ) -> PopulationMember | None:
        existing = self._population.get_by_experiment_ref(experiment_ref)
        if existing is not None:
            return existing  # idempotence (§17) — replay sûr, jamais un doublon.

        parent_a, parent_b = parents
        genome_version, genome_content, parent_member_id = self._propose_offspring_genome(
            specialty=specialty, parent_a=parent_a, parent_b=parent_b,
            experiment_ref=experiment_ref, use_crossover=policy.enable_crossover and index % 2 == 1,
            focus_genome_paths=focus_genome_paths,
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
        member = self._population.record_fitness(
            member.id, fitness, skill_candidate_id=skill_candidate_id, genome_candidate_id=genome_candidate_id,
        )
        # Gauntlet CHEAP (§14/§28 de la mission N+8) : AUCUNE exécution
        # supplémentaire — réutilise l'unique expérience déjà réelle de ce
        # membre (`result.eval_run`) pour produire un jugement structuré,
        # multi-dimensions, persisté (`gauntlet_results`). Calculé pour
        # CHAQUE membre, jamais seulement le candidat champion — c'est le
        # stage FULL (`_promote_champion_if_earned`) qui réserve les juges
        # coûteux (répétition réelle, HELDOUT) au seul candidat qui en a
        # besoin.
        self._run_cheap_gauntlet(
            specialty=specialty, generation=generation, member=member,
            eval_run=result.eval_run, limits=limits, fitness=fitness, config=config,
        )
        return member

    def _propose_offspring_genome(
        self,
        *,
        specialty: str,
        parent_a: PopulationMember | None,
        parent_b: PopulationMember | None,
        experiment_ref: str,
        use_crossover: bool,
        focus_genome_paths: frozenset[str] = frozenset(),
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
            mutated = mutate(
                content, rate=_DEFAULT_MUTATION_RATE, magnitude=_DEFAULT_MUTATION_MAGNITUDE, seed=seed,
                focus_paths=focus_genome_paths,
            )
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

        child_content = mutate(
            content_a, rate=_DEFAULT_MUTATION_RATE, magnitude=_DEFAULT_MUTATION_MAGNITUDE, seed=seed,
            focus_paths=focus_genome_paths,
        )
        version = self._genome.propose(
            content=child_content, created_by=Subject.CORE, origin=GenomeVersionOrigin.MUTATION,
            parent_version_id=parent_a.genome_version_id,
        )
        return version, child_content, parent_a.id

    def _build_evolution_context(self, specialty: str) -> EvolutionContext:
        """§8 de la mission N+9 — construit le contexte à partir de preuve
        RÉELLEMENT déjà persistée (`judge_learning_signals`), jamais une
        estimation. Ne fait QUE lire ; aucune écriture, aucun effet de
        bord — appelé une fois par `evolve_generation`, jamais mis en
        cache (les faiblesses évoluent à chaque génération)."""
        from apex.metalearner.curriculum_engine import CurriculumEngineService
        from apex.metalearner.judge_feedback_model import EvolutionContext
        from apex.metalearner.weakness_engine import WeaknessEngineService

        weaknesses = WeaknessEngineService(self._session_factory).aggregate(specialty)
        targets = CurriculumEngineService(self._session_factory).build_targets(specialty)
        focus_paths: set[str] = set()
        for target in targets:
            focus_paths.update(target.focus_genome_paths)
        return EvolutionContext(
            specialty=specialty, active_weaknesses=weaknesses, curriculum_targets=targets,
            focus_genome_paths=tuple(sorted(focus_paths)),
        )

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

        # Gauntlet FULL (passe N+8) : remplace l'ancien contrôle HELDOUT
        # booléen SEUL par une décision agrégée, multi-juges (correction,
        # efficacité, sécurité, régression vs champion, robustesse par
        # ré-exécution, généralisation HELDOUT) — un `REJECT`/`INCONCLUSIVE`
        # bloque la promotion exactement comme le faisait un échec HELDOUT
        # auparavant, jamais une moyenne qui masquerait un véto (§8/§12).
        gauntlet = self._run_full_gauntlet(specialty, generation, candidate, current_champion)
        if gauntlet is None or gauntlet.decision is not JudgeDecision.PASS:
            self._record_event(
                "evolution.gauntlet_rejected_champion_candidacy",
                {
                    "specialty": specialty, "generation": generation, "candidate_id": candidate.id,
                    "decision": gauntlet.decision.value if gauntlet is not None else "no_evidence",
                    "veto": gauntlet.veto if gauntlet is not None else False,
                    "veto_dimension": (
                        gauntlet.veto_dimension.value if gauntlet is not None and gauntlet.veto_dimension else None
                    ),
                },
            )
            return current_champion  # reste ELITE, jamais promu — aucune perte de données.

        # Benchmark de capacité INDÉPENDANT (passe N+10, §8/§12/§13) —
        # SEULEMENT après un Gauntlet FULL déjà PASS (§20 : jamais avant,
        # pour ne pas payer son coût réel pour un candidat déjà rejeté) ;
        # une régression critique de capacité vs le champion bloque la
        # promotion INDÉPENDAMMENT du Gauntlet PASS (§9 : « +15% correctness
        # -40% security -> PAS une amélioration acceptable »).
        capability = self._run_capability_benchmark_gate(specialty, generation, candidate, current_champion)
        if capability.decision is not JudgeDecision.PASS:
            self._record_event(
                "evolution.capability_benchmark_blocked_champion_candidacy",
                {
                    "specialty": specialty, "generation": generation, "candidate_id": candidate.id,
                    "decision": capability.decision.value, "reason": capability.reason,
                    "blocking_dimension": (
                        capability.blocking_dimension.value if capability.blocking_dimension else None
                    ),
                },
            )
            return current_champion  # reste ELITE, jamais promu — aucune perte de données.

        return self._population.set_status(candidate.id, MemberStatus.CHAMPION)

    def _run_capability_benchmark_gate(
        self, specialty: str, generation: int, candidate: PopulationMember,
        current_champion: PopulationMember | None,
    ) -> Any:
        """Isolation des pannes — même discipline que `_record_judge_signals` :
        une panne du benchmark de capacité ne doit JAMAIS lever hors de
        cette méthode ; elle devient une décision `INCONCLUSIVE` explicite
        (jamais un `PASS` par défaut, §3/§13 de la mission N+10)."""
        from apex.metalearner.capability_benchmark_service import CapabilityBenchmarkService
        from apex.metalearner.capability_model import PromotionRuleResult

        try:
            from apex.config.loader import load_config

            config = self._config or load_config()
            genome_content = self._genome.get_content(candidate.genome_version_id)
            limits = self._resolved_limits(genome_content)
            service = CapabilityBenchmarkService(self._session_factory, agent_runtime=self._agent_runtime)
            run = service.run_benchmark(
                specialty=specialty, member_id=candidate.id, generation=generation,
                genome_version_id=candidate.genome_version_id, genome_content=genome_content, limits=limits,
                compared_champion_id=current_champion.id if current_champion is not None else None,
            )
            if run is None:
                return PromotionRuleResult(
                    decision=JudgeDecision.INCONCLUSIVE,
                    reason="benchmark de capacité non exécuté (infrastructure).",
                )
            champion_delta = None
            if current_champion is not None:
                champion_run = service.get_latest(specialty, member_id=current_champion.id)
                champion_delta = service.compute_delta(
                    run, reference_run=champion_run, reference_kind="champion",
                )
            return service.decide_promotion(
                run, champion_delta, min_confidence=config.gauntlet.min_confidence_threshold,
            )
        except Exception as exc:
            self._record_event(
                "evolution.capability_benchmark_failed",
                {
                    "specialty": specialty, "candidate_id": candidate.id,
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
            )
            return PromotionRuleResult(
                decision=JudgeDecision.INCONCLUSIVE,
                reason=f"panne du benchmark de capacité : {exc.__class__.__name__}",
            )

    def _run_cheap_gauntlet(
        self,
        *,
        specialty: str,
        generation: int,
        member: PopulationMember,
        eval_run: EvalRun,
        limits: Any,
        fitness: FitnessResult,
        config: Any,
    ) -> GauntletResult:
        """Aucune exécution supplémentaire — voir la docstring du module et
        `apex.metalearner.judge_model.GauntletStage.CHEAP`. Le champion
        courant (s'il existe) fournit la base de comparaison de
        `RegressionJudge` — jamais un second calcul de fitness (§28)."""
        champion = self._population.get_champion(specialty)
        judge_input = JudgeInput(
            eval_run=eval_run, limits=limits, candidate_fitness_components=fitness.components,
            champion_member_id=champion.id if champion is not None else None,
            champion_fitness_components=champion.fitness_components if champion is not None else None,
            efficiency_reject_threshold=config.gauntlet.efficiency_reject_threshold,
            major_regression_factor=config.gauntlet.major_regression_factor,
        )
        runner = GauntletRunner(min_confidence_threshold=config.gauntlet.min_confidence_threshold)
        result = runner.run_cheap(judge_input, specialty=specialty, generation=generation, member_id=member.id)
        self._gauntlet_repo.create(result)
        # Judge Feedback (passe N+9) : un signal structuré par juge, jamais
        # un texte narratif seul — voir apex.metalearner.judge_feedback_service.
        self._record_judge_signals(result, genome_version_id=member.genome_version_id)
        return result

    def _run_full_gauntlet(
        self,
        specialty: str,
        generation: int,
        candidate: PopulationMember,
        current_champion: PopulationMember | None,
    ) -> GauntletResult | None:
        """`None` si le gauntlet CHEAP de ce candidat est introuvable —
        jamais une promotion silencieuse faute de preuve (chaque membre en
        reçoit pourtant TOUJOURS un à sa création, voir `_create_member`) :
        une preuve manquante est traitée comme un blocage, pas une
        autorisation par défaut (§23)."""
        from apex.config.loader import load_config
        from apex.evalrunner.repository import EvalRunRepository

        config = self._config or load_config()
        cheap_results = self._gauntlet_repo.list_by_member(candidate.id, limit=1)
        if not cheap_results:
            return None
        cheap_result = cheap_results[0]

        eval_run_id = _extract_eval_run_id(cheap_result)
        if eval_run_id is None:
            return None
        original_eval_run = EvalRunRepository(self._session_factory).get(eval_run_id)
        if original_eval_run is None:
            return None

        genome_content = self._genome.get_content(candidate.genome_version_id)
        limits = self._resolved_limits(genome_content)

        repeated_eval_run: EvalRun | None = None
        holdout_eval_run: EvalRun | None = None
        if config.gauntlet.enable_expensive_judges and not cheap_result.veto:
            # §13/§24 : jamais dépensées pour un candidat déjà rejeté par le
            # stage CHEAP — `GauntletRunner.run_full` s'arrêterait de toute
            # façon avant de les utiliser, mais les épargner ICI évite de
            # payer leur coût RÉEL (spawn + sandbox) pour rien.
            repeated_eval_run = self._repeat_execution(
                specialty=specialty, candidate=candidate, task_id=original_eval_run.task_id,
                genome_content=genome_content,
            )
            holdout_eval_run = self._run_holdout(specialty, candidate, generation, genome_content)

        judge_input = JudgeInput(
            eval_run=original_eval_run, limits=limits,
            candidate_fitness_components=candidate.fitness_components or {},
            repeated_eval_run=repeated_eval_run, holdout_eval_run=holdout_eval_run,
            champion_member_id=current_champion.id if current_champion is not None else None,
            champion_fitness_components=current_champion.fitness_components if current_champion is not None else None,
            efficiency_reject_threshold=config.gauntlet.efficiency_reject_threshold,
            major_regression_factor=config.gauntlet.major_regression_factor,
        )
        runner = GauntletRunner(min_confidence_threshold=config.gauntlet.min_confidence_threshold)
        full_result = runner.run_full(
            judge_input, specialty=specialty, generation=generation, member_id=candidate.id,
            cheap_result=cheap_result, compared_champion_id=current_champion.id if current_champion else None,
            run_expensive=config.gauntlet.enable_expensive_judges,
        )
        self._gauntlet_repo.create(full_result)
        # Seuls les jugements NOUVELLEMENT ajoutés par le stage FULL (les
        # juges EXPENSIVE) reçoivent un signal ici — les jugements CHEAP
        # réutilisés tels quels ont déjà été enregistrés par
        # `_run_cheap_gauntlet` ; les ré-enregistrer doublonnerait leur
        # poids dans l'agrégation de faiblesse (§4 de la mission N+9).
        new_judge_results = full_result.judge_results[len(cheap_result.judge_results):]
        self._record_judge_signals(
            full_result, genome_version_id=candidate.genome_version_id, judge_results=new_judge_results,
        )
        return full_result

    def _repeat_execution(
        self, *, specialty: str, candidate: PopulationMember, task_id: str, genome_content: dict[str, Any] | None,
    ) -> EvalRun | None:
        """`RobustnessJudge` (§7/§16 de la mission N+8) : ré-exécution
        RÉELLE de LA MÊME tâche avec LE MÊME Genome — appelle directement
        `EvalRunnerService.run` (Phase 5, inchangé), jamais `run_experiment_
        cycle` (qui générerait une NOUVELLE tâche à chaque appel via
        `TaskBankService.create` — jamais un vrai « repeat »). Un clone
        étant à usage unique, un clone FRAIS est spawné avec le MÊME
        `genome_ref`. `None` en cas de panne d'infrastructure — jamais
        susceptible de faire échouer la décision de promotion elle-même
        (`RobustnessJudge` traite `None` comme `INCONCLUSIVE`, jamais un
        `PASS` déduit)."""
        from apex.agents.pool_selection import pick_sandbox_capable_pool
        from apex.config.loader import load_config
        from apex.evalrunner.service import EvalRunnerService

        try:
            config = self._config or load_config()
            pool = pick_sandbox_capable_pool(config)
            genome_version = self._genome.get(candidate.genome_version_id)
            if genome_version is None:
                return None
            clone = self._agent_runtime.spawn(
                Subject.LEARNING, pool=pool, genome_ref=genome_version.content_hash, spawned_by=Subject.CORE,
            )
            return EvalRunnerService(self._session_factory, agent_runtime=self._agent_runtime).run(
                task_id=task_id, clone_id=clone.clone_id, method=EvalMethod.PRACTICE, requested_by=Subject.CORE,
                experiment_ref=f"{candidate.experiment_ref}:repeat", genome_content=genome_content,
            )
        except Exception as exc:
            self._record_event(
                "evolution.repeated_execution_failed",
                {"specialty": specialty, "candidate_id": candidate.id, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None

    def _run_holdout(
        self, specialty: str, candidate: PopulationMember, generation: int, genome_content: dict[str, Any] | None,
    ) -> EvalRun | None:
        """§13 : le contrôle HELDOUT réel avant toute promotion CHAMPION —
        INCHANGÉ depuis N+6, désormais exposé comme `EvalRun` (consommé par
        `GeneralizationJudge`) plutôt que réduit en interne à un booléen.
        Un clone étant à usage unique, ce contrôle spawn un clone FRAIS
        avec le MÊME Genome — jamais une réutilisation d'un clone déjà
        terminé."""
        from apex.agents.pool_selection import pick_sandbox_capable_pool
        from apex.config.loader import load_config

        try:
            config = self._config or load_config()
            pool = pick_sandbox_capable_pool(config)
            genome_version = self._genome.get(candidate.genome_version_id)
            if genome_version is None:
                return None
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
            return result.eval_run
        except Exception as exc:
            self._record_event(
                "evolution.holdout_check_failed",
                {"specialty": specialty, "candidate_id": candidate.id, "error": f"{exc.__class__.__name__}: {exc}"},
            )
            return None

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

    def _record_judge_signals(
        self, gauntlet_result: GauntletResult, *, genome_version_id: str, judge_results: Any = None,
    ) -> None:
        """Isolation des pannes — même discipline que `_process_feedback` :
        une panne d'enregistrement du feedback ne doit JAMAIS faire
        échouer la création d'un membre dont l'expérience/le jugement a
        déjà réellement eu lieu (§28 : le Judge System enrichit le
        pipeline, il ne doit jamais pouvoir le casser)."""
        from apex.metalearner.judge_feedback_service import JudgeFeedbackService

        try:
            JudgeFeedbackService(self._session_factory).record_signals(
                gauntlet_result, genome_version_id=genome_version_id, judge_results=judge_results,
            )
        except Exception as exc:
            self._record_event(
                "evolution.judge_feedback_failed",
                {
                    "specialty": gauntlet_result.specialty, "gauntlet_result_id": gauntlet_result.id,
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
            )

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
