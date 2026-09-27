"""Problèmes d'EXÉCUTION DE PROGRAMME (raisonnement multi-étapes vérifiable).

Un problème est un programme en ligne droite d'affectations DÉPENDANTES : chaque étape part de la
variable précédente (`b=a+4`), avec une constante ou une variable antérieure en second opérande.
La profondeur est le nombre d'opérations enchaînées. Le modèle GÉNÈRE la trace : la valeur de
chaque variable dans l'ordre, puis la réponse (`?c:a3,b7,c4=4`).

Deux programmes qui ne diffèrent que par les NOMS de variables ont la même forme canonique
(variables renommées dans l'ordre d'apparition) : c'est elle qui décide de la fraîcheur d'un
problème d'évaluation, pour que renommer ne suffise jamais à rendre « nouveau » un problème vu."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

#: Noms possibles (lettres sans ambiguïté visuelle, jamais de mot-clé Python).
NAMES = "abcdefghjkmnpqrstuvwxyz"
OPERATORS = "+-"


@dataclass(frozen=True)
class Problem:
    """Programme + trace attendue (valeurs de chaque variable, dans l'ordre)."""

    program: str
    names: tuple[str, ...]
    values: tuple[int, ...]

    @property
    def depth(self) -> int:
        return len(self.names) - 1

    @property
    def prompt(self) -> str:
        return f"{self.program}?{self.names[-1]}:"

    @property
    def answer(self) -> str:
        steps = ",".join(f"{n}{v}" for n, v in zip(self.names, self.values, strict=True))
        return f"{steps}={self.values[-1]}"

    @property
    def document(self) -> str:
        return self.prompt + self.answer

    @property
    def canonical(self) -> str:
        return canonical_form(self.program)

    def to_dict(self) -> dict[str, Any]:
        return {"program": self.program, "names": "".join(self.names), "values": list(self.values)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Problem:
        return cls(data["program"], tuple(data["names"]), tuple(data["values"]))


def generate(depth: int, rng: random.Random) -> Problem:
    if depth < 1:
        raise ValueError("profondeur >= 1")
    names = rng.sample(NAMES, depth + 1)
    env: dict[str, int] = {}
    first = rng.randint(0, 9)
    statements = [f"{names[0]}={first}"]
    env[names[0]] = first
    for i in range(1, depth + 1):
        previous = names[i - 1]
        op = rng.choice(OPERATORS)
        if i >= 2 and rng.random() < 0.4:
            other = rng.choice(names[: i - 1])
            operand, value = other, env[other]
        else:
            value = rng.randint(0, 9)
            operand = str(value)
        env[names[i]] = env[previous] + value if op == "+" else env[previous] - value
        statements.append(f"{names[i]}={previous}{op}{operand}")
    return Problem(";".join(statements), tuple(names), tuple(env[n] for n in names))


def interpret(program: str) -> Problem:
    """Évaluateur HÔTE indépendant du générateur (analyse du texte seul) ; refuse toute forme
    hors grammaire `nom=littéral` puis `nom=nom(+|-)(nom|littéral)`."""
    env: dict[str, int] = {}
    names: list[str] = []

    def operand(token: str) -> int:
        if token.isdigit() and len(token) == 1:
            return int(token)
        if token in env:
            return env[token]
        raise ValueError(f"opérande inconnu « {token} » dans {program!r}")

    for index, statement in enumerate(program.split(";")):
        target, _, expr = statement.partition("=")
        if len(target) != 1 or target not in NAMES or target in env:
            raise ValueError(f"affectation invalide « {statement} »")
        if index == 0:
            env[target] = operand(expr)
        else:
            if len(expr) != 3 or expr[1] not in OPERATORS or expr[0] != names[-1]:
                raise ValueError(f"expression invalide « {statement} »")
            left, right = env[expr[0]], operand(expr[2])
            env[target] = left + right if expr[1] == "+" else left - right
        names.append(target)
    if len(names) < 2:
        raise ValueError(f"programme trop court : {program!r}")
    return Problem(program, tuple(names), tuple(env[n] for n in names))


def canonical_form(program: str) -> str:
    """Variables renommées v0, v1… dans l'ordre d'apparition."""
    mapping: dict[str, str] = {}
    out = []
    for ch in program:
        if ch.isalpha():
            mapping.setdefault(ch, f"v{len(mapping)}")
            out.append(mapping[ch])
        else:
            out.append(ch)
    return "".join(out)


def as_function(problem: Problem, name: str) -> str:
    """Source Python exécutable dans le sandbox : le programme tel quel, puis la liste des valeurs
    de chaque variable (lue par Python, pas par l'évaluateur hôte)."""
    body = "".join(f"    {s}\n" for s in problem.program.split(";"))
    return f"def {name}():\n{body}    return [{', '.join(problem.names)}]\n"


_TRACE = re.compile(r"((?:[a-z]*-?\d+,)*[a-z]*-?\d+)=(-?\d+)")
_STEP = re.compile(r"([a-z]*)(-?\d+)")


def parse_trace(text: str) -> tuple[list[str], list[int], int] | None:
    """(noms écrits, valeurs écrites, réponse finale) si la sortie ENTIÈRE a la forme d'une trace ;
    None sinon (texte en trop, valeur manquante ou mal formée : jamais une réponse lue à moitié)."""
    match = _TRACE.fullmatch(text)
    if match is None:
        return None
    names, values = [], []
    for item in match.group(1).split(","):
        step = _STEP.fullmatch(item)
        if step is None:
            return None
        names.append(step.group(1))
        values.append(int(step.group(2)))
    return names, values, int(match.group(2))
