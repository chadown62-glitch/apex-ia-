"""`PopulationService` — persistance et bookkeeping d'une population de
clones expérimentaux (passe « Real Clone Population + Evolution Loop »).

Ce module NE spawn AUCUN clone, NE propose AUCUNE `GenomeVersion`, N'EXÉCUTE
AUCUNE expérience — il ne fait qu'enregistrer/dérouler le cycle de vie d'un
membre déjà créé ailleurs (`AgentRuntime.spawn`, `GenomeStoreService.
propose`, `apex.metalearner.cycle.run_experiment_cycle`), exactement le rôle
d'un repository/service de bookkeeping, jamais un second orchestrateur —
`apex.metalearner.evolution.PopulationEvolutionService` compose ce module
avec ces primitives déjà réelles, ce module ne les réimplémente jamais.

**`champion`/`elite`/`hibernating`/`retired` sont des étiquettes INTERNES à
la population, jamais une décision `PolicyEngine`** : elles ne changent
AUCUN droit, ne promeuvent AUCUNE `GenomeVersion`/`Skill` — seule une
décision HUMAINE explicite via `GenomeStoreService.promote`/
`SkillRegistryService` (inchangés) fait cela. Confondre les deux serait
exactement l'auto-illusion que §8/§19 de la mission interdisent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from apex.errors import PopulationCapExceeded
from apex.metalearner.population_fitness import FitnessResult
from apex.metalearner.population_model import MemberStatus, PopulationMember
from apex.metalearner.population_repository import PopulationMemberRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PopulationService:
    def __init__(self, session_factory: Any) -> None:
        self._repo = PopulationMemberRepository(session_factory)

    # -- création (idempotente + plafonnée) ----------------------------------

    def add_member(
        self,
        *,
        specialty: str,
        generation: int,
        clone_id: str,
        genome_version_id: str,
        parent_member_id: str | None,
        created_by: str,
        experiment_ref: str,
        max_population_size: int,
    ) -> PopulationMember:
        """Idempotent (§17) : rejouer le même `experiment_ref` renvoie le
        membre déjà créé, jamais un doublon — vérifié AVANT le plafond, pour
        qu'un replay reste toujours sûr même une fois la population pleine.
        `PopulationCapExceeded` seulement pour une création GENUINEMENT
        nouvelle au-delà de `max_population_size` membres actifs."""
        existing = self._repo.get_by_experiment_ref(experiment_ref)
        if existing is not None:
            return existing

        active = self._repo.count_active(specialty)
        if active >= max_population_size:
            raise PopulationCapExceeded(specialty, active=active, limit=max_population_size)

        member = PopulationMember.new(
            specialty=specialty, generation=generation, clone_id=clone_id,
            genome_version_id=genome_version_id, parent_member_id=parent_member_id,
            created_by=created_by, experiment_ref=experiment_ref,
        )
        self._repo.create(member)
        return member

    # -- résultat -------------------------------------------------------------

    def record_fitness(
        self,
        member_id: str,
        fitness: FitnessResult,
        *,
        skill_candidate_id: str | None = None,
        genome_candidate_id: str | None = None,
    ) -> PopulationMember:
        updated = self._repo.record_fitness(
            member_id, fitness=fitness.value, fitness_components=fitness.components,
            skill_candidate_id=skill_candidate_id, genome_candidate_id=genome_candidate_id,
        )
        if updated is None:
            raise LookupError(f"membre de population « {member_id} » introuvable")
        return updated

    def set_status(self, member_id: str, status: MemberStatus) -> PopulationMember:
        """`RETIRED` est terminal — `PopulationMemberRepository.set_status`
        refuse silencieusement (renvoie `None`) toute transition PARTANT
        d'un membre déjà `retired`, jamais une exception qui interromprait
        une génération pour un appel redondant (idempotent par construction,
        même esprit que §17)."""
        updated = self._repo.set_status(member_id, status=status, decided_at=_utcnow())
        if updated is None:
            current = self._repo.get(member_id)
            if current is None:
                raise LookupError(f"membre de population « {member_id} » introuvable")
            return current  # déjà `retired` — no-op, jamais une erreur.
        return updated

    # -- lecture --------------------------------------------------------------

    def get(self, member_id: str) -> PopulationMember | None:
        return self._repo.get(member_id)

    def list_by_specialty(self, specialty: str) -> list[PopulationMember]:
        return self._repo.list_by_specialty(specialty)

    def get_by_experiment_ref(self, experiment_ref: str) -> PopulationMember | None:
        """Lecture publique du même contrôle d'idempotence qu'`add_member`
        applique en interne — utile à un appelant (`apex.metalearner.
        evolution.PopulationEvolutionService`) qui veut éviter de dépenser
        le coût RÉEL d'une expérimentation (spawn + sandbox + oracle) avant
        même de savoir si ce `experiment_ref` a déjà été traité."""
        return self._repo.get_by_experiment_ref(experiment_ref)

    def count_active(self, specialty: str) -> int:
        """Lecture publique du même compteur que le plafond d'`add_member`
        applique en interne — permet à un appelant de vérifier le plafond
        AVANT de spawner un clone, jamais après."""
        return self._repo.count_active(specialty)

    def latest_generation(self, specialty: str) -> int:
        members = self._repo.list_by_specialty(specialty, limit=1)
        return members[0].generation if members else -1

    def get_champion(self, specialty: str) -> PopulationMember | None:
        champions = [m for m in self._repo.list_by_specialty(specialty) if m.status is MemberStatus.CHAMPION]
        return champions[0] if champions else None

    def list_elite(self, specialty: str) -> list[PopulationMember]:
        return [m for m in self._repo.list_by_specialty(specialty) if m.status is MemberStatus.ELITE]

    def get_lineage(self, member_id: str) -> list[PopulationMember]:
        """Remonte `parent_member_id` jusqu'à la racine — le membre demandé
        en premier, l'ancêtre le plus lointain en dernier. Borné par le
        nombre réel de membres traversés (pas de limite arbitraire) ; une
        boucle serait un bug du Core (jamais produite par `add_member`, qui
        ne référence qu'un `parent_member_id` déjà persisté), détectée ici
        plutôt que de boucler indéfiniment."""
        lineage: list[PopulationMember] = []
        seen: set[str] = set()
        current_id: str | None = member_id
        while current_id is not None and current_id not in seen:
            seen.add(current_id)
            member = self._repo.get(current_id)
            if member is None:
                break
            lineage.append(member)
            current_id = member.parent_member_id
        return lineage
