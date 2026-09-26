"""Tâches FRAÎCHES de la capacité CODE REPAIR : fonctions écrites APRÈS le gel
du monde de démonstration, absentes de tout monde, de tout corpus
d'entraînement et de toute suite du benchmark. Elles servent une seule
question : un champion promu par la boucle répare-t-il mieux que
l'initialisation des bugs qu'il n'a JAMAIS pu voir, même indirectement (la
sélection des expériences a été faite sur le held-out du monde, jamais sur
celles-ci) ?

Mêmes règles que `corpus.py` : fonctions pures, entrées de test seulement,
sorties calculées par exécution sandbox de la référence."""

from __future__ import annotations

from typing import Any

from apex.capabilities.code_repair.benchmark import (
    RepairSuite,
    build_benchmark,
    code_tokens,
    ngrams,
    similarity,
)
from apex.capabilities.code_repair.corpus import ReferenceFunction, _fn
from apex.sandbox.provider import SandboxProvider

FRESH: tuple[ReferenceFunction, ...] = (
    _fn(
        "count_uppercase",
        "strings",
        """
def count_uppercase(text):
    total = 0
    for ch in text:
        if ch.isupper():
            total += 1
    return total
""",
        ("Hello World",),
        ("",),
        ("abc",),
        ("ABC def G",),
    ),
    _fn(
        "pad_left",
        "strings",
        """
def pad_left(text, width, fill):
    if len(text) >= width:
        return text
    return fill * (width - len(text)) + text
""",
        ("7", 3, "0"),
        ("abc", 2, "*"),
        ("", 4, "-"),
        ("ab", 3, "x"),
    ),
    _fn(
        "longest_run",
        "lists",
        """
def longest_run(values):
    best = 0
    current = 0
    previous = None
    for v in values:
        if v == previous:
            current += 1
        else:
            current = 1
        previous = v
        if current > best:
            best = current
    return best
""",
        ([1, 1, 2, 2, 2, 3],),
        ([],),
        ([5],),
        ([4, 4, 4, 4],),
        ([1, 2, 1, 2],),
    ),
    _fn(
        "drop_every",
        "lists",
        """
def drop_every(values, n):
    out = []
    for i, v in enumerate(values):
        if (i + 1) % n != 0:
            out.append(v)
    return out
""",
        ([1, 2, 3, 4, 5, 6], 2),
        ([1, 2, 3], 1),
        ([], 3),
        ([7, 8, 9, 10], 3),
    ),
    _fn(
        "lcm",
        "math",
        """
def lcm(a, b):
    x, y = a, b
    while y:
        x, y = y, x % y
    return a * b // x
""",
        (4, 6),
        (7, 3),
        (5, 5),
        (12, 18),
    ),
    _fn(
        "count_divisors",
        "math",
        """
def count_divisors(n):
    count = 0
    for d in range(1, n + 1):
        if n % d == 0:
            count += 1
    return count
""",
        (1,),
        (12,),
        (13,),
        (36,),
    ),
    _fn(
        "nearest_value",
        "search_sort",
        """
def nearest_value(values, target):
    best = None
    for v in values:
        if best is None:
            best = v
            continue
        gap, best_gap = abs(v - target), abs(best - target)
        if gap < best_gap or (gap == best_gap and v < best):
            best = v
    return best
""",
        ([1, 5, 9], 6),
        ([], 3),
        ([4, 8], 6),
        ([3, -3], 0),
        ([10], -5),
        ([2, 7, 7, 12], 8),
    ),
    _fn(
        "selection_sort",
        "search_sort",
        """
def selection_sort(values):
    items = list(values)
    for i in range(len(items)):
        smallest = i
        for j in range(i + 1, len(items)):
            if items[j] < items[smallest]:
                smallest = j
        items[i], items[smallest] = items[smallest], items[i]
    return items
""",
        ([3, 1, 2],),
        ([],),
        ([5, 4, 3, 2, 1],),
        ([2, 2, 1],),
    ),
    _fn(
        "sum_values_above",
        "dicts",
        """
def sum_values_above(mapping, threshold):
    total = 0
    for key in sorted(mapping):
        if mapping[key] > threshold:
            total += mapping[key]
    return total
""",
        ({"a": 1, "b": 5, "c": 10}, 4),
        ({}, 0),
        ({"x": 3}, 3),
        ({"x": -1, "y": 0}, -2),
    ),
    _fn(
        "count_keys_with_prefix",
        "dicts",
        """
def count_keys_with_prefix(mapping, prefix):
    count = 0
    for key in mapping:
        if key.startswith(prefix):
            count += 1
    return count
""",
        ({"app_a": 1, "app_b": 2, "sys": 3}, "app"),
        ({}, "x"),
        ({"abc": 1}, ""),
        ({"b": 1, "ab": 2}, "b"),
    ),
    _fn(
        "parse_hex_color",
        "parsing",
        """
def parse_hex_color(text):
    if len(text) != 7 or text[0] != "#":
        raise ValueError("bad color")
    red = int(text[1:3], 16)
    green = int(text[3:5], 16)
    blue = int(text[5:7], 16)
    return (red, green, blue)
""",
        ("#ff0000",),
        ("#00ff7f",),
        ("#123456",),
        ("123456",),
        ("#12345",),
    ),
    _fn(
        "parse_time_of_day",
        "parsing",
        """
def parse_time_of_day(text):
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("bad time")
    hours = int(parts[0])
    minutes = int(parts[1])
    if hours < 0 or hours > 23 or minutes < 0 or minutes > 59:
        raise ValueError("out of range")
    return hours * 60 + minutes
""",
        ("00:00",),
        ("23:59",),
        ("12:30",),
        ("24:00",),
        ("9:60",),
        ("7",),
    ),
    _fn(
        "within_upload_limit",
        "security",
        """
def within_upload_limit(sizes, limit):
    total = 0
    for size in sizes:
        if size < 0:
            return False
        total += size
        if total > limit:
            return False
    return True
""",
        ([10, 20], 30),
        ([10, 21], 30),
        ([], 0),
        ([5, -1], 100),
        ([30], 30),
    ),
    _fn(
        "is_safe_identifier",
        "security",
        """
def is_safe_identifier(name, max_length):
    if not name or len(name) > max_length:
        return False
    if not name[0].isalpha():
        return False
    for ch in name:
        if not (ch.isalnum() or ch == "_"):
            return False
    return True
""",
        ("user_id", 10),
        ("1abc", 10),
        ("", 5),
        ("drop;table", 20),
        ("abcdef", 5),
        ("a_b", 3),
    ),
)

FRESH_SUITE = "cap:code_repair:fresh"


def build_fresh_suite(provider: SandboxProvider, *, bugs_per_function: int = 3) -> RepairSuite:
    """Même construction protégée que le benchmark (sorties par exécution de la référence, bugs
    gardés seulement s'ils sont détectés) ; toutes les instances forment UNE suite d'évaluation."""
    bench = build_benchmark(
        FRESH, provider, salt="apex-code-repair-fresh", bugs_per_function=bugs_per_function
    )
    instances = tuple(i for suite in bench.suites.values() for i in suite.instances)
    return RepairSuite(FRESH_SUITE, "fresh", tuple(sorted(instances, key=lambda i: i.instance_id)))


def fresh_contamination(world_texts: list[str], reference_sources: list[str]) -> dict[str, Any]:
    """Preuve que les tâches fraîches sont hors de portée du modèle : aucune séquence de 12 jetons
    de code partagée avec un texte du monde, et similarité maximale avec le corpus du benchmark."""
    fresh_grams = {f.fid: ngrams(code_tokens(f.source, normalize_names=False), 12) for f in FRESH}
    world_grams: set[tuple[str, ...]] = set()
    for text in world_texts:
        world_grams |= ngrams(code_tokens(text, normalize_names=False), 12)
    shared = sorted(fid for fid, grams in fresh_grams.items() if grams & world_grams)
    closest = max((similarity(f.source, src), f.fid) for f in FRESH for src in reference_sources)
    return {
        "shared_12gram_with_world": shared,
        "max_similarity_to_corpus": [round(closest[0], 3), closest[1]],
    }
