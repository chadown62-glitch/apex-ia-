"""Gabarit de conversation du Cerveau APEX v2 — le même à l'entraînement (instruction) et à l'inférence.

    <bos> <|system|> consignes <|end|> <|user|> message <|end|> <|assistant|> réponse <|end|> …

Les tokens de rôle sont placés PAR IDENTIFIANT : le texte d'un message ne peut jamais en produire (voir
`apex.brain.tokenizer`), donc un utilisateur ne peut pas se faire passer pour le système ou l'assistant. À
l'entraînement, seule la réponse de l'assistant (et son `<|end|>`, qui apprend au modèle à s'arrêter) compte
dans la perte : le modèle apprend à répondre, pas à imiter l'utilisateur."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from apex.brain.tokenizer import ByteBPETokenizer

ROLES = {
    "system": "<|system|>",
    "user": "<|user|>",
    "assistant": "<|assistant|>",
    "tool": "<|tool_result|>",
}
END = "<|end|>"


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"rôle inconnu : {self.role}")


def supports_chat(tokenizer: ByteBPETokenizer) -> bool:
    return all(tokenizer.has_special(token) for token in (*ROLES.values(), END))


def encode_chat(
    tokenizer: ByteBPETokenizer, messages: Sequence[Message], *, add_generation_prompt: bool = False
) -> tuple[list[int], list[int]]:
    """(identifiants, masque) — masque à 1 sur les jetons que le modèle doit apprendre à produire (contenu
    des réponses de l'assistant et leur `<|end|>`)."""
    if not supports_chat(tokenizer):
        raise ValueError("ce tokenizer n'a pas de tokens de conversation (Cerveau v1)")
    ids = [tokenizer.bos_id]
    mask = [0]
    end = tokenizer.special_id(END)
    for message in messages:
        content = tokenizer.encode(message.content)
        learn = 1 if message.role == "assistant" else 0
        ids.append(tokenizer.special_id(ROLES[message.role]))
        mask.append(0)
        ids.extend(content)
        mask.extend([learn] * len(content))
        ids.append(end)
        mask.append(learn)
    if add_generation_prompt:
        ids.append(tokenizer.special_id(ROLES["assistant"]))
        mask.append(0)
    return ids, mask


def stop_ids(tokenizer: ByteBPETokenizer) -> frozenset[int]:
    """Jetons qui terminent une réponse : `<|end|>`, `<eos>`, et tout début de tour (le modèle ne parle
    jamais à la place de l'utilisateur)."""
    stops = {tokenizer.eos_id}
    for token in (END, *ROLES.values()):
        if tokenizer.has_special(token):
            stops.add(tokenizer.special_id(token))
    return frozenset(stops)
