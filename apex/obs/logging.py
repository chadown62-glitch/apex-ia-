"""Logging structuré, stdlib uniquement.

Deux exigences non négociables :

1. **Rédaction des secrets** (§72). La rédaction est appliquée par le formateur,
   au dernier moment, et non laissée à la discipline des appelants. Un secret
   ne peut pas atteindre un fichier de log même si un composant le passe par
   erreur dans un champ de contexte.
2. **Contexte lié** (`bind`) pour que `task_id`, `run_id`, `genome_id` suivent
   l'exécution sans être repassés à chaque appel.

Stdlib uniquement : ce module doit fonctionner avant l'installation des
dépendances, puisque `apex doctor` en dépend.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

# Clés dont la valeur est systématiquement masquée, quel que soit son contenu.
SECRET_KEY_PATTERN = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key|dsn|conn(ection)?[_-]?string)",
    re.IGNORECASE,
)

# Motifs masqués y compris à l'intérieur d'une chaîne libre.
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"(?i)\b(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"(?i)(postgres(?:ql)?://[^:\s]+:)([^@\s]+)(@)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{16,})"),
)

REDACTED = "***redacted***"


def redact(value: Any) -> Any:
    """Masque récursivement les secrets d'une structure de données."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and SECRET_KEY_PATTERN.search(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        text = value
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.groups >= 3:
                text = pattern.sub(lambda m: m.group(1) + REDACTED + m.group(3), text)
            elif pattern.groups == 2:
                text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
            else:
                text = pattern.sub(REDACTED, text)
        return text
    return value


class JsonFormatter(logging.Formatter):
    """Une ligne JSON par événement, secrets masqués."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["ctx"] = context
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


class BoundLogger:
    """Logger portant un contexte immuable ; `bind` retourne un nouvel objet."""

    __slots__ = ("_logger", "_context")

    def __init__(self, logger: logging.Logger, context: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self._context = context or {}

    def bind(self, **fields: Any) -> BoundLogger:
        return BoundLogger(self._logger, {**self._context, **fields})

    def _log(self, level: int, msg: str, fields: dict[str, Any]) -> None:
        if not self._logger.isEnabledFor(level):
            return
        context = {**self._context, **fields} if fields else self._context
        self._logger.log(level, msg, extra={"context": context} if context else {})

    def debug(self, msg: str, **fields: Any) -> None:
        self._log(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._log(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._log(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._log(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields: Any) -> None:
        context = {**self._context, **fields}
        self._logger.error(msg, exc_info=True, extra={"context": context} if context else {})


_configured = False


def configure(level: str | None = None, *, stream: Any = None, json_output: bool = True) -> None:
    """Configure le logging racine. Idempotent."""
    global _configured
    root = logging.getLogger("apex")
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("APEX_LOG_LEVEL") or "INFO").upper())
    root.propagate = False
    _configured = True


def get_logger(name: str) -> BoundLogger:
    if not _configured:
        configure()
    return BoundLogger(logging.getLogger(f"apex.{name}"))
