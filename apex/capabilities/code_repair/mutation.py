"""Opérateurs d'édition d'UN jeton : ils servent à INJECTER les bugs du
benchmark et à ÉNUMÉRER les correctifs candidats.

Espace de réparation fermé (documenté, jamais masqué) : un candidat diffère
du code bogué par exactement une édition d'un jeton parmi
- comparateurs (`<` `<=` `>` `>=` `==` `!=`), opérateurs `+`/`-`, `+=`/`-=` ;
- `and`/`or`, `True`/`False`, `min`/`max` ;
- entier littéral n -> n±1 ;
- identifiant -> un autre identifiant du même code (mauvaise variable).
Chaque opérateur a son inverse dans l'ensemble : le correctif exact est
toujours énumérable (vérifié à la construction du benchmark).

Les éditions sont appliquées par position de caractères (le formatage du code
est conservé tel quel) et seuls les candidats qui COMPILENT sont gardés."""

from __future__ import annotations

import builtins
import io
import keyword
import tokenize
from dataclasses import dataclass

SWAPS: dict[str, tuple[str, ...]] = {
    "<": ("<=", ">"),
    "<=": ("<", ">="),
    ">": (">=", "<"),
    ">=": (">", "<="),
    "==": ("!=",),
    "!=": ("==",),
    "+": ("-",),
    "-": ("+",),
    "+=": ("-=",),
    "-=": ("+=",),
    "and": ("or",),
    "or": ("and",),
    "True": ("False",),
    "False": ("True",),
    "min": ("max",),
    "max": ("min",),
}
_BUILTINS = frozenset(dir(builtins))
_MAX_LITERAL = 20


@dataclass(frozen=True)
class Edit:
    offset: int
    old: str
    new: str
    line: int

    def describe(self) -> str:
        return f"ligne {self.line} : « {self.old} » -> « {self.new} »"

    def apply(self, source: str) -> str:
        return source[: self.offset] + self.new + source[self.offset + len(self.old) :]


def _tokens(source: str) -> list[tuple[int, str, int, int]]:
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.OP, tokenize.NAME, tokenize.NUMBER):
            out.append((tok.type, tok.string, starts[tok.start[0] - 1] + tok.start[1], tok.start[0]))
    return out


def single_edits(source: str) -> list[Edit]:
    """Toutes les éditions d'un jeton, dans l'ordre du texte (déterministe)."""
    toks = _tokens(source)
    identifiers = sorted(
        {
            s
            for kind, s, _, _ in toks
            if kind == tokenize.NAME and not keyword.iskeyword(s) and s not in _BUILTINS
        }
    )
    edits: list[Edit] = []
    for index, (kind, text, offset, line) in enumerate(toks):
        previous = toks[index - 1][1] if index else ""
        replacements = list(SWAPS.get(text, ()))
        if kind == tokenize.NUMBER and text.isdigit() and int(text) < _MAX_LITERAL:
            value = int(text)
            replacements += [str(value + 1)] + ([str(value - 1)] if value > 0 else [])
        if kind == tokenize.NAME and text in identifiers and previous not in (".", "def"):
            replacements += [other for other in identifiers if other != text]
        edits.extend(Edit(offset, text, new, line) for new in replacements)
    return edits


def compiles(source: str) -> bool:
    try:
        compile(source, "<candidate>", "exec")
    except (SyntaxError, ValueError):
        return False
    return True


def candidates(buggy: str) -> list[tuple[str, Edit]]:
    """Correctifs candidats uniques et compilables, dans un ordre déterministe."""
    seen: dict[str, Edit] = {}
    for edit in single_edits(buggy):
        text = edit.apply(buggy)
        if text not in seen and text != buggy and compiles(text):
            seen[text] = edit
    return sorted(seen.items(), key=lambda item: (item[1].offset, item[1].new, item[0]))
