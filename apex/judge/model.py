"""Vocabulaire fermé de `JudgeTier` — `JudgeTierLevel`, `JudgeVerdict`.

Registre : `JudgeTier`, « notation hiérarchisée tier 0/1/2 » (`phase_defined`
5, `phase_implemented` 9). Mapping de `docs/VISION_INTEGRATION.md` §16 :
« Le Verifier est le tier 0/1 ; le Critic est un rôle d'agent défini dans le
Genome » — TIER_2 est donc, par construction, un rôle de Clone
(`Subject.CRITIC`, déjà existant, `docs/COGNITIVE_ARCHITECTURE.md` §2.7 :
verdict **consultatif**, ne bloque pas seul), jamais une classe Python
`CriticEngine` à inventer ici.

`JudgeVerdict` ENVELOPPE un `apex.oracles.model.OracleResult`, ne le
remplace pas : le vocabulaire de correction (PASS/FAIL/ERROR/TIMEOUT/
UNKNOWN) reste entièrement celui d'`OracleProvider` — `JudgeTier` ajoute
seulement la métadonnée « quel palier a produit ce verdict, et pourquoi
n'est-on pas allé plus loin », jamais un second vocabulaire de correction
concurrent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from apex.oracles.model import OracleResult


class JudgeTierLevel(str, Enum):
    #: Objectif, déterministe — délègue entièrement à `OracleProvider`
    #: (`ExitCodeOracle` aujourd'hui). Aucune inférence de modèle, aucun
    #: jugement humain ou de clone.
    TIER_0 = "tier_0"
    #: Vérification automatisée étendue (le « Verifier » de
    #: `docs/VISION_INTEGRATION.md` §16) — ex. cross-check contre une
    #: seconde exécution, une propriété structurelle supplémentaire. PLANNED :
    #: aucune implémentation réelle n'existe encore derrière cette valeur.
    TIER_1 = "tier_1"
    #: Rôle de Clone (`Subject.CRITIC`), verdict consultatif qui ne bloque
    #: jamais seul (`docs/COGNITIVE_ARCHITECTURE.md` §2.7/§9). PLANNED :
    #: aucun raccordement à `AgentRuntime`/`Subject.CRITIC` n'existe encore.
    TIER_2 = "tier_2"


@dataclass(frozen=True)
class JudgeVerdict:
    """`tier` est le palier qui a RÉELLEMENT produit `oracle_result` — jamais
    le palier maximal théoriquement disponible. `notes` documente
    honnêtement pourquoi l'escalade vers un palier supérieur n'a pas eu
    lieu (aujourd'hui : parce qu'aucun palier supérieur n'est implémenté)."""

    tier: JudgeTierLevel
    oracle_result: OracleResult
    notes: str = ""
