"""Gabarit de conversation du Cerveau APEX v2 — le même à l'entraînement (instruction) et à l'inférence.

    <bos> <|system|> consignes <|end|> <|user|> message <|end|> <|assistant|> réponse <|end|> …

Les tokens de rôle sont placés PAR IDENTIFIANT : le texte d'un message ne peut jamais en produire (voir
`apex.brain.tokenizer`), donc un utilisateur ne peut pas se faire passer pour le système ou l'assistant. À
l'entraînement, seule la réponse de l'assistant (et son `<|end|>`, qui apprend au modèle à s'arrêter) compte
dans la perte : le modèle apprend à répondre, pas à imiter l'utilisateur.

**Calcul exact** (`apex.brain.calc`) : dans une réponse, le modèle écrit
`<|tool_call|> 974*89 <|tool_result|>` ; le moteur évalue l'expression et place le résultat à la suite, puis
le modèle continue. Dans les données d'entraînement, un appel est noté dans le texte de la réponse par
`CALL … RESULT … CALL_END` (caractères Unicode à usage privé ; seule une réponse de l'assistant est
interprétée, jamais un message de l'utilisateur) ; le résultat n'est PAS appris (le moteur le fournit),
l'appel l'est."""

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
TOOL_CALL = "<|tool_call|>"
TOOL_RESULT = "<|tool_result|>"
#: Notation d'un appel de calcul dans le texte d'une réponse (données d'entraînement uniquement).
CALL, RESULT, CALL_END = "\ue000", "\ue001", "\ue002"


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"rôle inconnu : {self.role}")


def supports_chat(tokenizer: ByteBPETokenizer) -> bool:
    return all(tokenizer.has_special(token) for token in (*ROLES.values(), END))


def supports_tools(tokenizer: ByteBPETokenizer) -> bool:
    return (
        supports_chat(tokenizer) and tokenizer.has_special(TOOL_CALL) and tokenizer.has_special(TOOL_RESULT)
    )


def calc_markup(expression: str) -> str:
    """Un appel noté pour les données d'entraînement, avec son résultat EXACT (calculé ici, par le même
    outil que le moteur d'inférence : la calculatrice, ou l'atelier 3D pour `vehicle(…)`, `weapon(…)`,
    `character(…)`)."""
    from apex.brain import calc, studio3d

    tool = studio3d if studio3d.is_call(expression) else calc
    return f"{CALL}{expression}{RESULT}{tool.run(expression)}{CALL_END}"


def tool_segments(content: str) -> list[tuple[str, str]]:
    """Découpe une réponse notée en segments `("text", …)`, `("call", expression)`, `("result", résultat)`.
    Une notation incomplète reste du texte."""
    segments: list[tuple[str, str]] = []
    rest = content
    while CALL in rest:
        before, _, after = rest.partition(CALL)
        expression, found_result, after_result = after.partition(RESULT)
        result, found_end, tail = after_result.partition(CALL_END)
        if not (found_result and found_end):
            break
        if before:
            segments.append(("text", before))
        segments += [("call", expression), ("result", result)]
        rest = tail
    if rest:
        segments.append(("text", rest))
    return segments


def visible_text(content: str) -> str:
    """Ce que l'utilisateur lit : le texte et les résultats des calculs, sans les expressions."""
    return "".join(value for kind, value in tool_segments(content) if kind != "call")


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
    tools = supports_tools(tokenizer)
    for message in messages:
        learn = 1 if message.role == "assistant" else 0
        ids.append(tokenizer.special_id(ROLES[message.role]))
        mask.append(0)
        if message.role == "assistant" and CALL in message.content:
            if tools:
                for kind, value in tool_segments(message.content):
                    if kind == "call":
                        piece = [
                            tokenizer.special_id(TOOL_CALL),
                            *tokenizer.encode(value),
                            tokenizer.special_id(TOOL_RESULT),
                        ]
                        ids.extend(piece)
                        mask.extend([learn] * len(piece))
                    else:  # le résultat est fourni par le moteur : jamais appris
                        piece = tokenizer.encode(value)
                        ids.extend(piece)
                        mask.extend([learn if kind == "text" else 0] * len(piece))
            else:
                content = tokenizer.encode(visible_text(message.content))
                ids.extend(content)
                mask.extend([learn] * len(content))
        else:
            content = tokenizer.encode(message.content)
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
