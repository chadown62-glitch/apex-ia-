"""Questions qui demandent une information ACTUELLE : météo, actualité, cours, heure, résultat d'hier.

Aucun modèle ne la connaît, quel qu'il soit : seul un outil réellement connecté peut la donner. Quand le
Cerveau APEX répond qu'il n'a pas accès au temps réel (`apex.brain.chat.cannot_know_kind` → `realtime`) à
une telle question, l'assistant ne la confie donc pas à un autre modèle comme s'il savait
(`apex.assistant.turns`) :
- un outil connecté est offert dans ce tour → la question va au moteur suivant, qui peut l'appeler ;
- sinon → la réponse honnête du Cerveau est gardée, et c'est dit.

Si la question ne porte pas sur l'actualité (« Quelle est la capitale de l'Australie ? »), le Cerveau a mal
classé une question de connaissance générale : un autre modèle peut la connaître, la délégation habituelle
s'applique.

Repérage par marqueurs explicites (mots du temps présent), écrits ici et mesurés (`tests/test_assistant.py`,
docs/CERVEAU_APEX_V2.md) : une question d'actualité sans aucun de ces mots n'est pas reconnue. Ce n'est pas
une compréhension de la question."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: Permission que portera un outil réellement connecté (lecture sur le réseau). Contrat PROPOSÉ (Issue #21)
#: : `apex.tools.model.Permission` (fichier partagé) n'en a pas aujourd'hui, donc aucun outil connecté
#: n'existe et `connected_tools` rend toujours une liste vide.
CONNECTED_PERMISSION = "network.read"

_MARKERS = (
    # français
    "aujourd'hui", "maintenant", "en ce moment", "actuellement",
    "actuel", "actuelle", "actuels", "actuelles",
    "ce matin", "ce midi", "ce soir", "cette nuit", "cette semaine", "ce week-end", "ce mois-ci",
    "cette année",
    "hier", "avant-hier", "demain", "dernier", "dernière", "derniers", "dernières", "récent", "récente",
    "récents", "récentes", "récemment", "vient de sortir", "en direct", "temps réel", "météo", "quel temps",
    "prévisions", "actualité", "actualités", "nouvelles du jour", "du jour", "cours du", "cours de la",
    "cours de l'", "taux de change", "bourse", "quelle heure", "quel jour sommes", "quelle date sommes",
    "score", "élections", "sondage", "trafic", "en ligne en ce moment", "fonctionne en ce moment",
    # anglais
    "today", "tonight", "this morning", "this evening", "this week", "this weekend", "this month",
    "this year",
    "right now", "now", "currently", "current", "latest", "recent", "recently", "yesterday", "tomorrow",
    "last night", "last week", "last weekend", "last month",
    "just released", "real-time", "real time", "weather", "forecast", "news", "headlines",
    "stock price", "exchange rate", "price of", "what time is it", "what day is it", "what's the date",
    "what is the date", "score", "election", "poll", "traffic", "trending",
)
_ALTERNATIVES = "|".join(re.escape(m) for m in sorted(set(_MARKERS), key=len, reverse=True))
_PATTERN = re.compile(r"(?<![\w])(?:" + _ALTERNATIVES + r")(?![\w])", re.IGNORECASE)
#: Apostrophe typographique (U+2019), ramenée à l'apostrophe droite des marqueurs.
_CURLY_APOSTROPHE = "\u2019"


def asks_for_current_information(question: str) -> bool:
    """La question contient-elle un marqueur explicite d'information actuelle ? (apostrophes typographiques
    normalisées ; insensible à la casse ; mots entiers)."""
    return _PATTERN.search(question.replace(_CURLY_APOSTROPHE, "'")) is not None


def connected_tools(specs: Iterable[Any]) -> list[str]:
    """Noms des outils offerts qui sont réellement connectés (permission `CONNECTED_PERMISSION`)."""
    return [spec.name for spec in specs
            if any(getattr(p, "value", p) == CONNECTED_PERMISSION for p in spec.permissions)]
