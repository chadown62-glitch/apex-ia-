"""Analyse de la requête (étape ANALYZE) et synthèse (étapes SYNTHESIZE/
DESIGN) — fonctions pures, déterministes, jamais une fabrication : chaque
champ produit est directement dérivable de son entrée (`provenance =
"deterministic-template"`). Aucun appel à `ModelProvider` dans cette
tranche v1 — documenté honnêtement dans `docs/AGENT_HANDOFF.md` comme
extension future possible, pas une simulation aujourd'hui."""

from __future__ import annotations

from dataclasses import replace

from apex.design.model import DesignBrief, DesignConcept, DesignRequest, ResearchResult, new_id

#: Vocabulaire de style fermé, reconnu par recherche de sous-chaîne
#: insensible à la casse dans le texte de la requête — jamais une inférence
#: de style non présente dans le texte fourni.
_STYLE_VOCABULARY: tuple[str, ...] = (
    "moderne", "urbain", "minimaliste", "industriel", "futuriste", "organique",
    "classique", "durable", "ludique", "audacieux", "naturel", "coloré",
    "monochrome", "rustique", "élégant", "brutaliste", "épuré",
)

_MAX_SUMMARY_LEN = 240


def analyze_request(request: DesignRequest) -> DesignBrief:
    """ANALYZE : dérive un `DesignBrief` initial de la seule requête —
    aucune recherche, aucun modèle, une extraction déterministe."""
    text = request.text.strip()
    if not text:
        raise ValueError("la requête de conception est vide")
    lowered = text.lower()
    style_keywords = tuple(kw for kw in _STYLE_VOCABULARY if kw in lowered)
    summary = text if len(text) <= _MAX_SUMMARY_LEN else text[: _MAX_SUMMARY_LEN - 3] + "..."
    return DesignBrief(
        design_id=request.id,
        summary=summary,
        goals=(text,),
        constraints=(),
        audience="non précisé — aucune extraction d'audience dans cette version",
        style_keywords=style_keywords,
        source_request=text,
        provenance="deterministic-template",
    )


def finalize_brief(brief: DesignBrief, research: tuple[ResearchResult, ...]) -> DesignBrief:
    """SYNTHESIZE : finalise le brief en y référant le volume de recherche
    réellement obtenu (0 si aucun backend n'était disponible) — jamais un
    contenu de recherche fabriqué dans le résumé."""
    if not research:
        return brief
    note = f" (recherche : {len(research)} résultat(s) pris en compte)"
    return replace(brief, summary=brief.summary + note)


def synthesize_concepts(
    brief: DesignBrief, research: tuple[ResearchResult, ...]
) -> tuple[DesignConcept, ...]:
    """DESIGN : produit au moins un `DesignConcept` versionné — un par
    mot-clé de style détecté (jusqu'à 3), ou un concept générique unique si
    aucun mot-clé de style n'a été détecté dans la requête."""
    based_on_research = tuple(range(len(research)))
    keywords = brief.style_keywords[:3] or ("générique",)
    concepts: list[DesignConcept] = []
    for version, keyword in enumerate(keywords, start=1):
        concepts.append(
            DesignConcept(
                id=new_id("concept"),
                design_id=brief.design_id,
                version=version,
                title=f"Concept {version} — registre {keyword}",
                narrative=(
                    f"Piste de conception pour « {brief.source_request} », "
                    f"dans un registre {keyword}."
                ),
                key_elements=(keyword,),
                based_on_research=based_on_research,
                provenance="deterministic-template",
            )
        )
    return tuple(concepts)
