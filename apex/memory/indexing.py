"""INDEXATION de la mémoire système (06/10/2026) : ce qui entre dans la mémoire, et sous quelle forme.

Règles anti-fuite pour les exécutions : un souvenir d'exécution ne contient QUE l'énoncé public de la tâche, le
code produit par le modèle, le verdict du Judge et les faits d'échec AUTORISÉS (`apex.experience.failure_facts`)
— jamais une entrée cachée, une valeur attendue, un harnais ni un message d'exception.
"""

from __future__ import annotations

from typing import Any

from apex.memory.model import MemoryCategory, MemoryKind, MemoryRecord, VerificationStatus
from apex.policy.model import Subject

CHUNK_CHARS = 1_500
CHUNK_OVERLAP = 200
_MAX_DOCUMENT_CHARS = 2_000_000
_MAX_CODE_CHARS = 4_000


def index_conversation_exchange(
    memory: Any, *, conversation_id: str, message_id: str, user_text: str, assistant_text: str,
) -> MemoryRecord:
    return memory.write_system(
        category=MemoryCategory.CONVERSATION, kind=MemoryKind.EPISODIC, created_by=Subject.HUMAN,
        content=f"Utilisateur : {user_text.strip()}\nAssistant : {assistant_text.strip()}",
        source=f"conversation:{conversation_id}:{message_id}",
    )


def chunk_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Découpe en morceaux d'au plus `size` caractères, en préférant les fins de paragraphe ou de phrase."""
    text = text.strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def index_document(memory: Any, *, title: str, content: str, document_id: str, created_by: Subject) -> list[MemoryRecord]:
    if not title.strip() or not content.strip():
        raise ValueError("un document exige un titre et un contenu")
    if len(content) > _MAX_DOCUMENT_CHARS:
        raise ValueError(f"document trop volumineux ({_MAX_DOCUMENT_CHARS} caractères au plus)")
    records = []
    for i, chunk in enumerate(chunk_text(content)):
        records.append(memory.write_system(
            category=MemoryCategory.KNOWLEDGE, kind=MemoryKind.SEMANTIC, created_by=created_by,
            content=f"{title.strip()}\n{chunk}", source=f"document:{document_id}#{i}",
        ))
    return records


def index_task_run(memory: Any, *, run: Any, task: Any, eval_runs: list[Any]) -> MemoryRecord | None:
    """Souvenir d'une TaskRun TERMINÉE (réussie ou non) — rien pour une exécution sans tentative jugée."""
    from apex.experience.failure_facts import extract_failure_facts, render_failure_facts
    from apex.experience.failure_taxonomy import classify_failure

    if not eval_runs:
        return None
    last = eval_runs[-1]
    category = classify_failure(last)
    verdict = getattr(last.oracle_verdict, "value", str(last.oracle_verdict))
    code = ((last.facts or {}).get("model_response") or {}).get("text") or ""
    lines = [
        f"Tâche : {task.title}",
        f"Énoncé : {task.description[:1_500]}",
        f"Résultat : {run.status.value} après {len(eval_runs)} tentative(s) jugée(s), dernier verdict {verdict}",
    ]
    if verdict == "pass":
        lines.append("Solution validée par le Judge (code produit) :\n" + code[:_MAX_CODE_CHARS])
        kind, status, confidence = MemoryKind.PROCEDURAL, VerificationStatus.VERIFIED, 0.9
    else:
        facts = extract_failure_facts(last, category)
        if facts is not None:
            lines.append(render_failure_facts(facts))
        lines.append("Dernière tentative (échouée) :\n" + code[:_MAX_CODE_CHARS])
        kind, status, confidence = MemoryKind.EXPERIMENTAL, VerificationStatus.FALSE, 0.6
    return memory.write_system(
        category=MemoryCategory.EXECUTION, kind=kind, created_by=Subject.CORE, content="\n".join(lines),
        source=f"task_run:{run.id}", verification_status=status, confidence=confidence,
    )


def index_workspace(memory: Any, *, run_id: str, workspace: Any, max_files: int = 50) -> list[MemoryRecord]:
    """Fichiers Python d'un projet (artefacts), découpés — provenance `workspace:<run>:<chemin>#<morceau>`."""
    records = []
    for rel, size in workspace.files()[:max_files]:
        if not rel.endswith(".py") or size > 200_000:
            continue
        text = workspace.read_bytes(rel).decode("utf-8", errors="replace")
        for i, chunk in enumerate(chunk_text(text)):
            records.append(memory.write_system(
                category=MemoryCategory.PROJECT, kind=MemoryKind.SEMANTIC, created_by=Subject.CORE,
                content=f"{rel}\n{chunk}", source=f"workspace:{run_id}:{rel}#{i}",
            ))
    return records
