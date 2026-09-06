"""Registre des `SpecialtyTaskProvider` (passe « Universal Task + Curriculum
Engine », N+11) — même patron que `apex.taskgen.provider.
get_default_generator` : un seul point de construction, rien d'autre.

Miroir volontairement PARTIEL d'`apex.specialties.registry` : seule
`research` a un provider RÉEL aujourd'hui (même raison que
`apex.specialties.registry.SPECIALTIES` — aucun moteur réel derrière une
autre spécialité, voir sa docstring). Ne pas enregistrer une spécialité ici
sans qu'un `SpecialtyTaskProvider` réel existe derrière (même discipline)."""

from __future__ import annotations

from apex.taskgen.specialty_provider import ResearchTaskProvider, SpecialtyTaskProvider

_PROVIDERS: dict[str, SpecialtyTaskProvider] = {
    "research": ResearchTaskProvider(),
}


def get_provider(specialty_id: str) -> SpecialtyTaskProvider | None:
    return _PROVIDERS.get(specialty_id)


def supported_specialty_ids() -> tuple[str, ...]:
    return tuple(_PROVIDERS.keys())
