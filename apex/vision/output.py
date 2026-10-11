"""Strict output validation. Malformed model responses never become successful analyses."""

from __future__ import annotations

import json

from apex.vision.contract import VisionError, VisualAnswer, VisualElement

VISUAL_KINDS = ("text", "equation", "table", "chart", "diagram", "object")
TEXT_LIMITS = {"extracted_text": 4000, "analysis": 600, "answer": 400}
ELEMENT_TEXT_LIMITS = {"label": 128, "description": 360}
LIST_TEXT_LIMITS = {"uncertainties": 240, "help_steps": 320}

# Also used during decoding: step calculations precede the final answer.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted_text": {"type": "string", "maxLength": TEXT_LIMITS["extracted_text"]},
        "visual_elements": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(VISUAL_KINDS)},
                    "label": {"type": "string", "maxLength": ELEMENT_TEXT_LIMITS["label"]},
                    "description": {"type": "string", "maxLength": ELEMENT_TEXT_LIMITS["description"]},
                },
                "required": ["kind", "label", "description"],
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string", "maxLength": TEXT_LIMITS["analysis"]},
        "uncertainties": {
            "type": "array",
            "items": {"type": "string", "maxLength": LIST_TEXT_LIMITS["uncertainties"]},
            "maxItems": 6,
        },
        "help_steps": {
            "type": "array",
            "items": {"type": "string", "maxLength": LIST_TEXT_LIMITS["help_steps"]},
            "maxItems": 6,
        },
        "answer": {"type": ["string", "null"], "maxLength": TEXT_LIMITS["answer"]},
    },
    "required": ["extracted_text", "visual_elements", "analysis", "uncertainties", "help_steps", "answer"],
    "additionalProperties": False,
}


def text_budget_hits(answer: VisualAnswer) -> tuple[str, ...]:
    """Report saturated fields without rewriting the generated content."""
    hits = [key for key, limit in TEXT_LIMITS.items() if len(getattr(answer, key) or "") >= limit]
    for key, limit in LIST_TEXT_LIMITS.items():
        hits.extend(f"{key}[{i}]" for i, value in enumerate(getattr(answer, key)) if len(value) >= limit)
    for i, element in enumerate(answer.visual_elements):
        hits.extend(
            f"visual_elements[{i}].{key}"
            for key, limit in ELEMENT_TEXT_LIMITS.items()
            if len(getattr(element, key)) >= limit
        )
    return tuple(hits)


def parse_answer(text: str) -> VisualAnswer:
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        obj = json.loads(text)
        keys = {"extracted_text", "visual_elements", "analysis", "uncertainties", "answer", "help_steps"}
        if not isinstance(obj, dict) or set(obj) != keys:
            raise ValueError("schema")
        if not all(isinstance(obj[k], str) for k in ("extracted_text", "analysis")):
            raise ValueError("text fields")
        if obj["answer"] is not None and not isinstance(obj["answer"], str):
            raise ValueError("answer")
        for key, limit in TEXT_LIMITS.items():
            if obj[key] is not None and len(obj[key]) > limit:
                raise ValueError("text budget")
        for k in ("uncertainties", "help_steps"):
            if (
                not isinstance(obj[k], list)
                or len(obj[k]) > 6
                or not all(isinstance(v, str) and len(v) <= LIST_TEXT_LIMITS[k] for v in obj[k])
            ):
                raise ValueError("list fields")
        elements = obj["visual_elements"]
        if not isinstance(elements, list) or len(elements) > 12:
            raise ValueError("elements")
        for element in elements:
            if (
                not isinstance(element, dict)
                or set(element) != {"kind", "label", "description"}
                or not all(isinstance(v, str) for v in element.values())
                or element.get("kind") not in VISUAL_KINDS
                or any(len(element[k]) > limit for k, limit in ELEMENT_TEXT_LIMITS.items())
            ):
                raise ValueError("element")
        if not obj["analysis"].strip():
            raise ValueError("empty analysis")
        return VisualAnswer(
            obj["extracted_text"],
            tuple(VisualElement(**e) for e in elements),
            obj["analysis"],
            tuple(obj["uncertainties"]),
            obj["answer"],
            tuple(obj["help_steps"]),
        )
    except (ValueError, TypeError, RecursionError) as exc:
        raise VisionError("invalid_output", "Le modèle n'a pas produit le contrat JSON attendu.") from exc
