"""Exercices de CONNAISSANCE tirés de textes écrits par des humains (français et anglais) — complément des
exercices calculés (`apex.neural.tasks`).

- **Commandes et fonctions** : la ligne NOM/NAME des pages de manuel du corpus (« chown - Modifier le
  propriétaire… ») -> « À quoi sert la commande `chown` ? ».
- **Bibliothèque standard Python** : la première phrase de la documentation de chaque fonction publique
  (lue sur l'interpréteur local) -> « What does `textwrap.dedent` do? ».
- **Traduction** : les traductions humaines anglais <-> français des logiciels (catalogues gettext du
  corpus).

Évaluation honnête : pour une connaissance, on mesure le RAPPEL — la même connaissance, posée avec une
formulation jamais vue à l'entraînement ; pour la traduction, la GÉNÉRALISATION — des phrases jamais vues.
La notation est un recouvrement de mots avec la référence (`check: overlap`), pas une égalité exacte.
"""

# Les tirages servent à varier des exercices de façon reproductible, pas à la cryptographie.
# ruff: noqa: S311

from __future__ import annotations

import hashlib
import importlib
import inspect
import random
import re
from collections.abc import Iterator
from pathlib import Path

from apex.neural.corpus import iter_documents
from apex.neural.tasks import TaskExample, with_system

_FR_NAME = re.compile(r"^NOM\n\s+(.+?)(?:\n\n|\nSYNOPSIS)", re.M | re.S)
_EN_NAME = re.compile(r"^NAME\n\s+(.+?)(?:\n\n|\nSYNOPSIS|\nLIBRARY)", re.M | re.S)
_SECTION = re.compile(r"/man(\d)[^/]*/")
_KIND_FR = {
    "1": "la commande",
    "8": "la commande d'administration",
    "2": "l'appel système",
    "3": "la fonction C",
    "4": "le périphérique",
    "5": "le fichier",
    "6": "le jeu",
    "7": "la notion",
}
_KIND_EN = {
    "1": "command",
    "8": "administration command",
    "2": "system call",
    "3": "C function",
    "4": "device",
    "5": "file",
    "6": "game",
    "7": "concept",
}
STDLIB_MODULES = (
    "os",
    "os.path",
    "shutil",
    "pathlib",
    "json",
    "re",
    "math",
    "random",
    "statistics",
    "itertools",
    "functools",
    "collections",
    "datetime",
    "time",
    "textwrap",
    "string",
    "subprocess",
    "sys",
    "glob",
    "csv",
    "hashlib",
    "base64",
    "uuid",
    "tempfile",
    "logging",
    "argparse",
    "heapq",
    "bisect",
    "copy",
    "difflib",
    "fractions",
    "decimal",
    "operator",
    "urllib.parse",
    "html",
    "zipfile",
    "gzip",
    "sqlite3",
    "inspect",
    "typing",
    "dataclasses",
    "enum",
    "contextlib",
    "threading",
    "queue",
    "socket",
    "unicodedata",
    "calendar",
)


def _hash_bucket(key: str, modulo: int = 100) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % modulo


def man_page_entries(corpus_dir: Path) -> Iterator[tuple[str, str, str, str]]:
    """(langue, section, noms, description) de chaque page de manuel du corpus."""
    for document in iter_documents(
        corpus_dir, ["manpages-fr", "manpages-fr-dev", "linux-manpages", "linux-manpages-dev"]
    ):
        french = document.lang == "fr"
        match = (_FR_NAME if french else _EN_NAME).search(document.text)
        section = _SECTION.search(document.path)
        if match is None or section is None:
            continue
        line = " ".join(match.group(1).split())
        if " - " not in line:
            continue
        names, description = line.split(" - ", 1)
        if not 8 <= len(description) <= 220:
            continue
        yield ("fr" if french else "en"), section.group(1), names.strip(), description.strip().rstrip(".")


def man_page_examples(corpus_dir: Path, *, split: str) -> list[TaskExample]:
    """Entraînement : deux formulations ; évaluation : une troisième, jamais vue."""
    examples = []
    seen: set[tuple[str, str]] = set()
    for lang, section, names, description in man_page_entries(corpus_dir):
        name = names.split(",")[0].strip()
        if (lang, name) in seen or not re.fullmatch(r"[\w.+-]{1,40}", name):
            continue
        seen.add((lang, name))
        if lang == "fr":
            kind = _KIND_FR.get(section, "la page")
            questions = (
                f"À quoi sert {kind} `{name}` ?",
                f"Que fait {kind} {name} sous Linux ?",
                f"Explique-moi brièvement {kind} `{name}`.",
            )
            answer = f"`{names}` : {description[0].upper()}{description[1:]}."
        else:
            kind = _KIND_EN.get(section, "page")
            questions = (
                f"What does the `{name}` {kind} do?",
                f"What is the Linux {kind} {name} used for?",
                f"Briefly explain the `{name}` {kind}.",
            )
            answer = f"`{names}`: {description[0].upper()}{description[1:]}."
        chosen = questions[2:] if split == "eval" else questions[:2]
        for question in chosen:
            examples.append(
                TaskExample(
                    "knowledge_command",
                    lang,
                    question,
                    answer,
                    {"type": "overlap", "answer": description, "threshold": 0.6},
                    meta={"name": name},
                )
            )
    return examples


def _first_sentence(doc: str) -> str | None:
    paragraph = doc.strip().split("\n\n")[0]
    paragraph = " ".join(paragraph.split()).replace("``", "`")  # balisage reStructuredText
    match = re.match(r"(.+?[.!?])(?:\s|$)", paragraph)
    sentence = match.group(1) if match else paragraph
    if not 15 <= len(sentence) <= 220 or sentence.startswith(("Deprecated", "DEPRECATED", ">>>")):
        return None
    return sentence


def stdlib_entries(modules: tuple[str, ...] = STDLIB_MODULES) -> Iterator[tuple[str, str]]:
    """(nom qualifié, première phrase de la documentation) des fonctions et classes publiques."""
    for module_name in modules:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for attribute, value in sorted(vars(module).items()):
            if attribute.startswith("_") or not (
                inspect.isfunction(value) or inspect.isbuiltin(value) or inspect.isclass(value)
            ):
                continue
            root = module_name.split(".")[0]
            if (getattr(value, "__module__", None) or module_name).split(".")[0] not in (root, f"_{root}"):
                continue  # réexport d'un autre module
            sentence = _first_sentence(inspect.getdoc(value) or "")
            if sentence is not None:
                yield f"{module_name}.{attribute}", sentence


def stdlib_examples(*, split: str) -> list[TaskExample]:
    examples = []
    for name, sentence in stdlib_entries():
        if split == "eval":
            pairs = ((f"Explain what `{name}` does in Python.", "en"),)
        else:
            pairs = ((f"What does `{name}` do in Python?", "en"), (f"Que fait `{name}` en Python ?", "fr"))
        for question, lang in pairs:
            answer = (
                f"`{name}` : d'après sa documentation, « {sentence} »"
                if lang == "fr"
                else f"`{name}`: {sentence}"
            )
            examples.append(
                TaskExample(
                    "knowledge_python",
                    lang,
                    question,
                    answer,
                    {"type": "overlap", "answer": sentence, "threshold": 0.6},
                    meta={"name": name},
                )
            )
    return examples


_PLACEHOLDER = re.compile(r"%|\{|\}|\\|<|>|_[A-Za-z]|&[A-Za-z]|\t")


def translation_pairs(corpus_dir: Path) -> Iterator[tuple[str, str]]:
    seen: set[str] = set()
    for document in iter_documents(corpus_dir, ["traductions-fr", "traductions-gnome-fr"]):
        for block in document.text.split("\n\n"):
            lines = block.strip().split("\n")
            if len(lines) != 2:
                continue
            english, french = (line.strip() for line in lines)
            if english in seen or english == french or _PLACEHOLDER.search(english + french):
                continue
            if not (20 <= len(english) <= 160 and 20 <= len(french) <= 200):
                continue
            letters = min(sum(c.isalpha() for c in text) / len(text) for text in (english, french))
            if letters < 0.6 or not english[0].isupper():
                continue  # soulignements « ===== », listes de symboles…
            seen.add(english)
            yield english, french


def translation_examples(
    corpus_dir: Path, *, split: str, limit: int = 40_000, seed: int = 0
) -> list[TaskExample]:
    """Paires réservées à l'évaluation (environ 3 %, par empreinte) : jamais vues à l'entraînement."""
    rng = random.Random(seed)
    examples = []
    for english, french in translation_pairs(corpus_dir):
        in_eval = _hash_bucket(english) < 3
        if (split == "eval") != in_eval:
            continue
        if rng.random() < 0.5:
            question = rng.choice(("Traduis en français : « {} »", "Comment dit-on en français : « {} » ?"))
            examples.append(
                TaskExample(
                    "translation",
                    "fr",
                    question.format(english),
                    french,
                    {"type": "overlap", "answer": french, "threshold": 0.5},
                )
            )
        else:
            question = rng.choice(('Translate into English: "{}"', 'How do you say this in English: "{}"'))
            examples.append(
                TaskExample(
                    "translation",
                    "en",
                    question.format(french),
                    english,
                    {"type": "overlap", "answer": english, "threshold": 0.5},
                )
            )
        if len(examples) >= limit:
            break
    return examples


def knowledge_examples(
    corpus_dir: Path, *, split: str, eval_per_family: int = 40, seed: int = 0
) -> list[TaskExample]:
    """Tous les exercices de connaissance d'un split, avec les consignes système vues à l'usage ;
    l'évaluation est échantillonnée (`eval_per_family`)."""
    families = [
        man_page_examples(corpus_dir, split=split),
        stdlib_examples(split=split),
        translation_examples(corpus_dir, split=split, seed=seed),
    ]
    rng = random.Random(f"{split}:{seed}")
    if split == "eval":
        families = [rng.sample(family, min(eval_per_family, len(family))) for family in families]
    return [with_system(example, rng) for family in families for example in family]
