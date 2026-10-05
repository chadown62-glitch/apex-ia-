"""Validation INDÉPENDANTE des 10 tâches ajoutées pour le protocole v2 (docs/PROTOCOLE_V2.md §3).

Pour chaque tâche : une solution écrite à la main PASSE (vraie sandbox, vrai Judge) et une solution qui tombe
dans le piège de l'énoncé ÉCHOUE avec « cas incorrects ». Prouve que les tâches sont bien formées — jamais
leur difficulté pour un modèle réel (c'est la calibration RUN 1 qui la MESURE).
"""

from __future__ import annotations

import ast

import pytest

from apex.taskgen.solution_tasks import _CONTROLLED_TASKS, REFERENCE_TASK_KINDS, build_reference_task_spec
from tests.test_experience_conditioning import _REFERENCE_SOLUTIONS
from tests.test_experience_conditioning import (
    test_each_controlled_task_passes_a_correct_solution_and_fails_its_trap as _check,
)
from tests.test_taskloop_execution import sqlite_session_factory

V2_TASKS = (
    "roman_to_int",
    "merge_intervals",
    "luhn_valid",
    "balanced",
    "camel_to_snake",
    "median",
    "chunk",
    "parse_duration",
    "title_case",
    "dedupe_keep_last",
)

_V2_SOLUTIONS = {
    "roman_to_int": (
        "def roman_to_int(s):\n    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n"
        "    if not s or any(c not in v for c in s):\n        return None\n    total = 0\n"
        "    for i, c in enumerate(s):\n        if i + 1 < len(s) and v[c] < v[s[i + 1]]:\n            total -= v[c]\n"
        "        else:\n            total += v[c]\n    return total\n",
        # piège : minuscules acceptées
        "def roman_to_int(s):\n    s = s.upper()\n    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n"
        "    if not s or any(c not in v for c in s):\n        return None\n    total = 0\n"
        "    for i, c in enumerate(s):\n        if i + 1 < len(s) and v[c] < v[s[i + 1]]:\n            total -= v[c]\n"
        "        else:\n            total += v[c]\n    return total\n",
    ),
    "merge_intervals": (
        "def merge_intervals(iv):\n    out = []\n    for a, b in sorted(iv):\n"
        "        if out and a <= out[-1][1]:\n            out[-1][1] = max(out[-1][1], b)\n"
        "        else:\n            out.append([a, b])\n    return out\n",
        # piège : intervalles qui se touchent non fusionnés
        "def merge_intervals(iv):\n    out = []\n    for a, b in sorted(iv):\n"
        "        if out and a < out[-1][1]:\n            out[-1][1] = max(out[-1][1], b)\n"
        "        else:\n            out.append([a, b])\n    return out\n",
    ),
    "luhn_valid": (
        "def luhn_valid(s):\n    s = s.replace(' ', '')\n    if len(s) < 2 or not s.isdigit():\n        return False\n"
        "    total = 0\n    for i, c in enumerate(reversed(s)):\n        d = int(c)\n        if i % 2:\n"
        "            d = d * 2 - 9 if d * 2 > 9 else d * 2\n        total += d\n    return total % 10 == 0\n",
        # piège : longueur minimale oubliée
        "def luhn_valid(s):\n    s = s.replace(' ', '')\n    if not s.isdigit():\n        return False\n"
        "    total = 0\n    for i, c in enumerate(reversed(s)):\n        d = int(c)\n        if i % 2:\n"
        "            d = d * 2 - 9 if d * 2 > 9 else d * 2\n        total += d\n    return total % 10 == 0\n",
    ),
    "balanced": (
        "def balanced(s):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    stack = []\n    for c in s:\n"
        "        if c in '([{':\n            stack.append(c)\n        elif c in pairs:\n"
        "            if not stack or stack.pop() != pairs[c]:\n                return False\n    return not stack\n",
        # piège : seuls les comptes sont vérifiés, pas l'imbrication
        "def balanced(s):\n    return all(s.count(a) == s.count(b) for a, b in ('()', '[]', '{}'))\n",
    ),
    "camel_to_snake": (
        "def camel_to_snake(s):\n    import re\n    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\\1_\\2', s)\n"
        "    s = re.sub(r'([a-z0-9])([A-Z])', r'\\1_\\2', s)\n    return s.lower()\n",
        # piège : chaque majuscule devient un mot
        "def camel_to_snake(s):\n    out = ''\n    for i, c in enumerate(s):\n"
        "        out += ('_' if c.isupper() and i else '') + c.lower()\n    return out\n",
    ),
    "median": (
        "def median(nums):\n    if not nums:\n        return None\n    s = sorted(nums)\n    m = len(s) // 2\n"
        "    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2\n",
        # piège : liste non triée
        "def median(nums):\n    if not nums:\n        return None\n    s = nums\n    m = len(s) // 2\n"
        "    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2\n",
    ),
    "chunk": (
        "def chunk(lst, n):\n    if n <= 0:\n        return []\n    return [lst[i:i + n] for i in range(0, len(lst), n)]\n",
        # piège : n <= 0 non traité (range lève ValueError pour 0)
        "def chunk(lst, n):\n    return [lst[i:i + n] for i in range(0, len(lst), n)]\n",
    ),
    "parse_duration": (
        "def parse_duration(s):\n    import re\n    m = re.fullmatch(r'(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?', s)\n"
        "    if not s or not m:\n        return None\n    h, mi, se = (int(g) if g else 0 for g in m.groups())\n"
        "    return h * 3600 + mi * 60 + se\n",
        # piège : ordre non vérifié
        "def parse_duration(s):\n    import re\n    parts = re.findall(r'(\\d+)([hms])', s)\n    if not parts:\n"
        "        return None\n    return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[u] for n, u in parts)\n",
    ),
    "title_case": (
        "def title_case(s):\n    small = {'de', 'la', 'le', 'et', 'du', 'des'}\n    words = s.split(' ')\n"
        "    return ' '.join(w.lower() if i and w.lower() in small else w.capitalize() for i, w in enumerate(words))\n",
        # piège : petits mots en tête aussi en minuscules
        "def title_case(s):\n    small = {'de', 'la', 'le', 'et', 'du', 'des'}\n"
        "    return ' '.join(w.lower() if w.lower() in small else w.capitalize() for w in s.split(' '))\n",
    ),
    "dedupe_keep_last": (
        "def dedupe_keep_last(items):\n    out = []\n    for x in reversed(items):\n        if x not in out:\n"
        "            out.append(x)\n    return out[::-1]\n",
        # piège : première occurrence conservée
        "def dedupe_keep_last(items):\n    out = []\n    for x in items:\n        if x not in out:\n"
        "            out.append(x)\n    return out\n",
    ),
}


@pytest.fixture
def factory():
    return sqlite_session_factory()


def test_the_task_set_is_the_5_existing_plus_the_10_new() -> None:
    assert set(V2_TASKS) == set(_V2_SOLUTIONS)
    assert set(REFERENCE_TASK_KINDS) == {"is_palindrome", *_REFERENCE_SOLUTIONS, *V2_TASKS}
    assert len(REFERENCE_TASK_KINDS) == 15


@pytest.mark.parametrize("kind", V2_TASKS)
def test_each_v2_task_passes_a_correct_solution_and_fails_its_trap(factory, kind, monkeypatch) -> None:
    import tests.test_experience_conditioning as conditioning

    monkeypatch.setitem(conditioning._REFERENCE_SOLUTIONS, kind, _V2_SOLUTIONS[kind])
    _check(factory, kind)


@pytest.mark.parametrize("kind", V2_TASKS)
def test_v2_tasks_never_show_a_hidden_case_or_answer_in_the_statement(kind) -> None:
    spec = build_reference_task_spec(kind)
    _, _, cases = _CONTROLLED_TASKS[kind]
    assert len(cases) == 8 and len({repr(args) for args, _ in cases}) == 8
    for args, _ in cases:
        for arg in args:
            if isinstance(arg, str) and len(arg) >= 3:
                assert f"« {arg} »" not in spec.description, (
                    arg
                )  # l'exemple de l'énoncé n'est pas un cas caché
    assert spec.expected_answer not in spec.description
    assert "_APEX_INPUTS" not in spec.description
    ast.parse(spec.solution_harness)
