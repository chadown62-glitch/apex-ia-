"""Validation des arguments d'un outil — sous-ensemble EXPLICITE de JSON Schema (aucune dépendance) :
`type` (object, string, integer, number, boolean, array), `properties`, `required`, `additionalProperties`
(faux par défaut ici : un argument inconnu est refusé), `enum`, `minLength`/`maxLength`, `minimum`/`maximum`,
`items`, `maxItems`. Un mot-clé non pris en charge dans un schéma déclaré est une erreur de programmation."""

from __future__ import annotations

from typing import Any

_SUPPORTED = {
    "type", "properties", "required", "additionalProperties", "enum", "minLength", "maxLength", "minimum",
    "maximum", "items", "maxItems", "description", "default",
}
_TYPES = {
    "object": dict, "string": str, "array": list, "boolean": bool,
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Liste des violations (vide : valide)."""
    unsupported = set(schema) - _SUPPORTED
    if unsupported:
        raise ValueError(f"mots-clés de schéma non pris en charge : {sorted(unsupported)}")
    errors: list[str] = []
    kind = schema.get("type")
    if kind is not None and not _is_type(value, kind):
        return [f"{path} : type {type(value).__name__} au lieu de {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} : valeur hors de {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path} : trop court")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path} : trop long ({len(value)} > {schema['maxLength']})")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} : < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} : > {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path} : plus de {schema['maxItems']} éléments")
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} : requis")
        if not schema.get("additionalProperties", False):
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key} : argument inconnu")
        for key, sub in properties.items():
            if key in value:
                errors.extend(validate(value[key], sub, f"{path}.{key}"))
    return errors


def _is_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[kind])


def check_schema(schema: dict[str, Any]) -> None:
    """Refuse à l'enregistrement un schéma qui utilise un mot-clé non pris en charge (récursivement)."""
    unsupported = set(schema) - _SUPPORTED
    if unsupported:
        raise ValueError(f"mots-clés de schéma non pris en charge : {sorted(unsupported)}")
    for sub in schema.get("properties", {}).values():
        check_schema(sub)
    if isinstance(schema.get("items"), dict):
        check_schema(schema["items"])
