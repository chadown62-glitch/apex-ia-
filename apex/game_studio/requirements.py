"""Independent, conservative requirement coverage; never an aesthetic judge.

The extractor is deterministic and grounded in user text, not in the proposed
plan. Unknown semantics remain explicitly unverified. A matching task label
cannot qualify a builder for an unsupported building type or visual feature.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


# Canonical nouns, not arbitrary substring matches. This scope is public.
NOUNS = {
    "vehicle": r"voitures?|vehicules?|cars?|vehicles?|coupes?|bolides?",
    "building": (
        r"batiments?|buildings?|garages?|galeries?|galler(?:y|ies)|fleuristes?|florists?|"
        r"bibliotheques?|libraries|library|pharmacies|pharmacie|boulangeries?|bakeries|bakery"
    ),
    "chair": r"fauteuils?|chaises?|chairs?|armchairs?|sieges?",
}
NUMBERS = {
    "un": 1,
    "une": 1,
    "a": 1,
    "one": 1,
    "deux": 2,
    "two": 2,
    "trois": 3,
    "three": 3,
    "quatre": 4,
    "four": 4,
    "cinq": 5,
    "five": 5,
    "six": 6,
    "ten": 10,
    "dix": 10,
}
SPECIALIZED = {
    "gallery": r"galeri\w*|galler\w*",
    "florist": r"fleurist\w*|florist\w*",
    "library": r"bibliothequ\w*|librar\w*",
    "pharmacy": r"pharmac\w*",
    "bakery": r"boulanger\w*|baker\w*",
}
FEATURES = {
    "red": r"rouges?|red",
    "blue": r"bleu\w*|blue",
    "leather": r"cuir|leather",
    "wood": r"bois|wood\w*",
    "low_poly": r"low[ -]poly",
    "cartoon": r"cartoon|anime",
    "no_glass": r"sans (?:aucun )?vitres?|without (?:any )?glass",
}
UNSUPPORTED = r"outils?|tools?|velos?|bicycles?|tables?|arbres?|trees?|fleurs?|flowers?|machines?|robots?"


def extract(request: dict[str, Any]) -> dict[str, Any]:
    source = request["description"]
    text = normalize(source)
    requirements = []
    for kind, pattern in NOUNS.items():
        # Count explicit quantified mentions; unquantified repeated nouns are
        # treated as potentially anaphoric, not silently counted twice.
        mentions = []
        for match in re.finditer(r"\b(?:" + pattern + r")\b", text):
            before = text[max(0, match.start() - 45) : match.start()]
            count = re.search(r"\b(" + "|".join(NUMBERS) + r"|\d+)\s+(?:\w+\s+){0,2}$", before)
            value = count.group(1) if count else None
            mentions.append(
                (
                    match.group(),
                    NUMBERS.get(value, int(value) if value and value.isdigit() else 1),
                    bool(count),
                )
            )
        if mentions:
            counted = [m[1] for m in mentions if m[2]]
            requirements.append(
                {
                    "id": kind,
                    "kind": kind,
                    "count": sum(counted) if counted else 1,
                    "source_terms": sorted({m[0] for m in mentions}),
                    "mandatory": True,
                }
            )
    for group, patterns in (("context", SPECIALIZED), ("feature", FEATURES)):
        for key, pattern in patterns.items():
            match = re.search(r"\b(?:" + pattern + r")\b", text)
            if match:
                requirements.append(
                    {
                        "id": key,
                        "kind": group,
                        "quote": match.group(),
                        "pattern": pattern,
                        "mandatory": True,
                    }
                )
    for match in re.finditer(r"\b(?:" + UNSUPPORTED + r")\b", text):
        requirements.append(
            {
                "id": "unsupported_" + match.group(),
                "kind": "unsupported",
                "quote": match.group(),
                "mandatory": True,
            }
        )
    return {
        "schema_version": 1,
        "source": source,
        "requirements": requirements,
        "scope": "French/English explicit entity nouns, counts, listed contexts and features",
        "unverified": [
            "unlisted semantics",
            "artistic fidelity",
            "reference pixels",
            "implicit preferences",
        ],
        "reference_count": request.get("reference_count", 0),
    }


def judge(contract: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(s["kind"] for s in steps)
    defects, unavailable, covered = [], [], []
    requested_kinds = {item["kind"] for item in contract["requirements"]}
    for kind in NOUNS:
        if counts[kind] and kind not in requested_kinds:
            defects.append(
                {
                    "requirement": kind,
                    "defect": "unrequested_object",
                    "correction": "Remove unrequested " + kind + " assets",
                }
            )
    for item in contract["requirements"]:
        kind = item["kind"]
        if kind in NOUNS:
            if counts[kind] != item["count"]:
                defects.append(
                    {
                        "requirement": item["id"],
                        "defect": "quantity_mismatch",
                        "expected": item["count"],
                        "observed": counts[kind],
                        "correction": f"Create exactly {item['count']} independent {kind} tasks",
                    }
                )
            else:
                covered.append(item["id"])
        elif kind in ("context", "feature"):
            candidates = [s for s in steps if s["kind"] == "building"] if kind == "context" else steps
            if not any(
                re.search(r"\b(?:" + item["pattern"] + r")\b", normalize(s["description"]))
                for s in candidates
            ):
                defects.append(
                    {
                        "requirement": item["id"],
                        "defect": "omission_or_substitution",
                        "source": item["quote"],
                        "correction": "Preserve requirement; disclose unavailable implementation",
                    }
                )
            else:
                covered.append(item["id"])
            if kind == "context" or item["id"] in ("low_poly", "cartoon", "no_glass"):
                unavailable.append({"requirement": item["id"], "reason": "builder_feature_not_qualified"})
        else:
            disclosed = any(
                s["kind"] == "unsupported" and item["quote"] in normalize(s["description"]) for s in steps
            )
            if not disclosed:
                defects.append(
                    {
                        "requirement": item["id"],
                        "defect": "unsupported_object_not_disclosed",
                        "correction": "Add an unsupported task for " + item["quote"],
                    }
                )
            unavailable.append({"requirement": item["id"], "reason": "no_builder_for_requested_object"})
    # A negated entity is not a positive requirement. Do not silently guess.
    if re.search(
        r"\b(?:sans|without|aucun|no)\s+(?:" + "|".join(NOUNS.values()) + r")\b",
        normalize(contract["source"]),
    ):
        defects.append(
            {
                "requirement": "negation",
                "defect": "ambiguous_or_contradictory_entity_request",
                "correction": "Negative entities exceed this qualified scope",
            }
        )
    return {
        "verdict": "fail" if defects else "pass_in_declared_scope",
        "defects": defects,
        "covered": covered,
        "unavailable": unavailable,
        "unverified": contract["unverified"],
        "method": "independent_source_anchored_rules_v1",
        "artistic_fidelity_certified": False,
    }


def material_defects(
    contract: dict[str, Any], hierarchy: dict[str, Any], family: str
) -> list[dict[str, Any]]:
    """Qualified property: unlinked vehicle paint RGB channel dominance only.

    This does not measure perceived rendered color under lighting or aesthetics.
    Thresholds are fixed before trials: dominant channel >= .15 and >= 1.5x
    the other two. No reference image matching is claimed.
    """
    colors = {item["id"] for item in contract["requirements"]} & {"red", "blue"}
    if family != "vehicle" or len(colors) != 1:
        return []
    color = next(iter(colors))
    index = 0 if color == "red" else 2
    pbr = hierarchy.get("chassis", {}).get("pbr", {})
    for name, material in pbr.items():
        node = material.get("Base Color", {})
        value = node.get("value", [])
        if (
            not node.get("linked")
            and len(value) >= 3
            and value[index] >= 0.15
            and value[index] >= 1.5 * max(value[i] for i in range(3) if i != index)
        ):
            continue
        return [
            {
                "id": "requested_paint_channels",
                "object": "chassis",
                "material": name,
                "reference": "explicit_user_" + color,
                "observed": value,
                "correction": "Set ONLY material "
                + name
                + " base_color to "
                + ("[0.5, 0.02, 0.02]" if color == "red" else "[0.02, 0.06, 0.5]"),
                "expected_improvement": "Requested dominant RGB channel in saved shader",
                "confidence": 1.0,
                "confidence_scope": "numeric shader property only",
                "verification": "Unlinked base color: dominant channel >=0.15 and >=1.5x other channels",
            }
        ]
    return (
        []
        if pbr
        else [
            {
                "id": "requested_paint_unverifiable",
                "object": "chassis",
                "correction": "Restore an inspectable chassis paint shader",
            }
        ]
    )
