"""Mémoire SYSTÈME d'APEX : ajouter un document, chercher par pertinence (06/10/2026).

`POST /memory/documents` découpe et indexe un texte fourni par l'utilisateur (catégorie `knowledge`) ;
`GET /memory/search` renvoie les extraits les plus pertinents (BM25 lexical, `apex.memory.retrieval`), avec
leur provenance et leur score — exactement ce que le Chat et le TaskLoop reçoivent quand ils consultent la
mémoire. Agit sous l'identité HUMAN : la politique (`WRITE_SYSTEM_MEMORY`/`READ_SYSTEM_MEMORY`) reste
l'autorité ; aucun clone n'y a accès."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from apex.api.schemas import DataEnvelope

router = APIRouter(tags=["memory"])


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)


@router.post("/memory/documents", response_model=DataEnvelope[dict[str, Any]])
def add_document(body: DocumentIn) -> DataEnvelope[dict[str, Any]]:
    from apex.api.dependencies import get_session_factory
    from apex.memory.indexing import index_document
    from apex.memory.service import MemoryService
    from apex.policy.model import Subject

    document_id = str(uuid.uuid4())
    try:
        records = index_document(MemoryService(get_session_factory()), title=body.title, content=body.content,
                                 document_id=document_id, created_by=Subject.HUMAN)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataEnvelope(status="real", data={
        "document_id": document_id, "chunks": len(records), "record_ids": [r.id for r in records],
    })


@router.get("/memory/search", response_model=DataEnvelope[list[dict[str, Any]]])
def search_memory(
    q: str = Query(min_length=1, max_length=2000),
    categories: list[str] | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=20),
) -> DataEnvelope[list[dict[str, Any]]]:
    from apex.api.dependencies import get_session_factory
    from apex.memory.model import MemoryCategory
    from apex.memory.service import MemoryService
    from apex.policy.model import Subject

    try:
        wanted = [MemoryCategory(c) for c in categories] if categories else list(MemoryCategory)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"catégorie inconnue : {exc}") from exc
    items = MemoryService(get_session_factory()).retrieve(
        q, requested_by=Subject.HUMAN, categories=wanted, limit=limit, max_snippet_chars=600)
    return DataEnvelope(status="real", data=[i.to_dict() for i in items])
