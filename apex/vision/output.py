"""Strict output validation. Malformed model responses never become successful analyses."""

from __future__ import annotations

import json

from apex.vision.contract import VisionError, VisualAnswer, VisualElement

# Also used during decoding: step calculations precede the final answer.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted_text": {"type": "string"},
        "visual_elements": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["kind", "label", "description"],
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string"},
        "uncertainties": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "help_steps": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "answer": {"type": ["string", "null"]},
    },
    "required": ["extracted_text", "visual_elements", "analysis", "uncertainties", "help_steps", "answer"],
    "additionalProperties": False,
}


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
        for k in ("uncertainties", "help_steps"):
            if (
                not isinstance(obj[k], list)
                or len(obj[k]) > 100
                or not all(isinstance(v, str) for v in obj[k])
            ):
                raise ValueError("list fields")
        elements = obj["visual_elements"]
        if not isinstance(elements, list) or len(elements) > 100:
            raise ValueError("elements")
        for element in elements:
            if (
                not isinstance(element, dict)
                or set(element) != {"kind", "label", "description"}
                or not all(isinstance(v, str) for v in element.values())
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
