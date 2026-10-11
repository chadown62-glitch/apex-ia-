# ruff: noqa: E501, RUF001, S311
# E501 : gabarits de phrases (une consigne et sa réponse par ligne, plus lisibles entières) ; RUF001 : le
# signe de multiplication (U+00D7) est voulu ; S311 : `random` génère des exercices, aucun usage de
# sécurité.
"""Exercices d'instruction générés par APEX lui-même, à réponse VÉRIFIABLE (français et anglais).

Chaque exemple est une conversation (`messages`) dont la réponse est vraie PAR CONSTRUCTION : calculée
(arithmétique, conversions, dates, listes), obtenue en EXÉCUTANT le programme (sortie d'un programme, tests
d'une fonction), ou écrite à la main par l'équipe d'APEX (identité, limites, procédures). Aucun texte
produit par un autre modèle d'IA. Chaque exemple porte un `check` qui permet de noter une réponse du
modèle :

- `exact` : la réponse attendue (nombre, mot, liste) doit apparaître ;
- `code` : le code Python de la réponse est exécuté contre des tests (bac à sable : processus séparé) ;
- `json` : la réponse contient un objet JSON égal à l'attendu ;
- `refusal` : la réponse doit reconnaître la limite (pas d'accès, aucune action faite, information
manquante) ;
- `none` : exemple d'entraînement sans notation automatique (explications).

Le jeu d'ÉVALUATION est tiré avec d'autres graines et ne partage aucune consigne avec l'entraînement ; une
partie des fonctions à écrire n'apparaît JAMAIS à l'entraînement (mesure de généralisation, pas de mémoire).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

SYSTEM_FR = (
    "Tu es le Cerveau APEX, un modèle de langage construit et entraîné de zéro par APEX, exécuté en local. "
    "Réponds avec exactitude et concision. Si une information te manque ou si tu ne sais pas, dis-le."
)
SYSTEM_EN = (
    "You are the APEX Brain, a language model built and trained from scratch by APEX, running locally. "
    "Answer accurately and concisely. If information is missing or you do not know, say so."
)


def _assistant_instructions() -> str:
    from apex.assistant.modes import MODES

    return MODES["normal"].instructions


@dataclass(frozen=True)
class TaskExample:
    family: str
    lang: str
    user: str
    assistant: str
    check: dict[str, Any]
    history: tuple[tuple[str, str], ...] = ()  # tours précédents (utilisateur, assistant)
    system: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        for user, assistant in self.history:
            messages += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        messages += [
            {"role": "user", "content": self.user},
            {"role": "assistant", "content": self.assistant},
        ]
        return messages

    def key(self) -> str:
        return hashlib.sha256(
            json.dumps([self.history, self.user], ensure_ascii=False).encode()
        ).hexdigest()

    def to_row(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "lang": self.lang,
            "messages": self.messages(),
            "check": self.check,
            "key": self.key(),
            **({"meta": self.meta} if self.meta else {}),
        }


def _pick(rng: random.Random, options: list[str] | tuple[str, ...]) -> str:
    return options[rng.randrange(len(options))]


def _fr_int(n: int) -> str:
    return str(n)


# --------------------------------------------------------------------------------------------- arithmétique


def arithmetic(rng: random.Random, lang: str) -> TaskExample:
    op = _pick(rng, ("+", "-", "*", "//", "%"))
    digits = rng.choice((1, 2, 2, 3, 3, 4))
    a = rng.randint(10 ** (digits - 1) if digits > 1 else 0, 10**digits - 1)
    b = rng.randint(1, 10 ** rng.choice((1, 2, digits)) - 1)
    if op == "-" and rng.random() < 0.7 and b > a:
        a, b = b, a
    operations: dict[str, Callable[[int, int], int]] = {
        "+": lambda x, y: x + y,
        "-": lambda x, y: x - y,
        "*": lambda x, y: x * y,
        "//": lambda x, y: x // y,
        "%": lambda x, y: x % y,
    }
    if op in ("//", "%") and b == 0:
        b = 1
    result = operations[op](a, b)
    if lang == "fr":
        symbol = {"+": "+", "-": "-", "*": "×", "//": "÷", "%": "mod"}[op]
        if op == "//":
            user = _pick(
                rng,
                (
                    f"Quel est le quotient entier de {a} par {b} ?",
                    f"Combien font {a} ÷ {b} (division entière) ?",
                ),
            )
        elif op == "%":
            user = _pick(
                rng, (f"Quel est le reste de la division de {a} par {b} ?", f"Calcule {a} modulo {b}.")
            )
        else:
            user = _pick(
                rng,
                (
                    f"Combien font {a} {symbol} {b} ?",
                    f"Calcule {a} {symbol} {b}.",
                    f"Quel est le résultat de {a} {symbol} {b} ?",
                    f"{a} {symbol} {b} = ?",
                ),
            )
        steps = _multiplication_steps(a, b, "×") if op == "*" and b >= 10 and a >= 10 else ""
        if op == "//":
            answer = f"{a} ÷ {b} = {result} (reste {a % b})."
        elif op == "%":
            answer = f"{a} = {b} × {a // b} + {result}, donc le reste est {result}."
        else:
            answer = f"{steps}{a} {symbol} {b} = {result}."
    else:
        symbol = {"+": "+", "-": "-", "*": "×", "//": "÷", "%": "mod"}[op]
        if op == "//":
            user = _pick(
                rng,
                (
                    f"What is the integer quotient of {a} divided by {b}?",
                    f"What is {a} ÷ {b} (integer division)?",
                ),
            )
        elif op == "%":
            user = _pick(rng, (f"What is the remainder of {a} divided by {b}?", f"Compute {a} mod {b}."))
        else:
            user = _pick(
                rng,
                (
                    f"What is {a} {symbol} {b}?",
                    f"Compute {a} {symbol} {b}.",
                    f"Calculate {a} {symbol} {b}.",
                    f"{a} {symbol} {b} = ?",
                ),
            )
        steps = _multiplication_steps(a, b, "×") if op == "*" and b >= 10 and a >= 10 else ""
        if op == "//":
            answer = f"{a} ÷ {b} = {result} (remainder {a % b})."
        elif op == "%":
            answer = f"{a} = {b} × {a // b} + {result}, so the remainder is {result}."
        else:
            answer = f"{steps}{a} {symbol} {b} = {result}."
    return TaskExample("arithmetic", lang, user, answer, {"type": "exact", "answer": str(result)})


def _multiplication_steps(a: int, b: int, symbol: str) -> str:
    parts = []
    for position, digit in enumerate(reversed(str(b))):
        if digit != "0":
            parts.append(int(digit) * 10**position)
    if len(parts) < 2:
        return ""
    parts.reverse()
    decomposition = " + ".join(f"{a} {symbol} {p}" for p in parts)
    partials = " + ".join(str(a * p) for p in parts)
    return f"{a} {symbol} {b} = {decomposition} = {partials}\n"


def word_problem(rng: random.Random, lang: str) -> TaskExample:
    names_fr = ("Marie", "Lucas", "Inès", "Hugo", "Léa", "Nora", "Paul", "Chloé", "Sami", "Jade")
    names_en = ("Anna", "Ben", "Clara", "David", "Emma", "Felix", "Grace", "Henry", "Ivy", "Jack")
    items_fr = (
        ("pommes", "pomme"),
        ("billes", "bille"),
        ("livres", "livre"),
        ("crayons", "crayon"),
        ("timbres", "timbre"),
    )
    items_en = (
        ("apples", "apple"),
        ("marbles", "marble"),
        ("books", "book"),
        ("pencils", "pencil"),
        ("stamps", "stamp"),
    )
    a = rng.randint(5, 60)
    b = rng.randint(1, a - 1)
    c = rng.randint(1, 30)
    kind = rng.choice(("give_buy", "groups", "price"))
    if lang == "fr":
        name = _pick(rng, names_fr)
        plural, _ = items_fr[rng.randrange(len(items_fr))]
        if kind == "give_buy":
            user = f"{name} a {a} {plural}. Elle en donne {b}, puis en achète {c}. Combien de {plural} a-t-elle maintenant ?"
            result = a - b + c
            answer = f"Elle commence avec {a}, en donne {b} : {a} - {b} = {a - b}. Elle en achète {c} : {a - b} + {c} = {result}. Elle a maintenant {result} {plural}."
        elif kind == "groups":
            groups = rng.randint(2, 9)
            each = rng.randint(2, 12)
            result = groups * each
            user = f"{name} range des {plural} dans {groups} boîtes de {each}. Combien de {plural} y a-t-il en tout ?"
            answer = f"{groups} boîtes × {each} {plural} = {result}. Il y a {result} {plural} en tout."
        else:
            price = rng.randint(2, 15)
            count = rng.randint(2, 9)
            result = price * count
            user = f"Un article coûte {price} euros. Combien coûtent {count} articles ?"
            answer = f"{count} × {price} = {result}. Les {count} articles coûtent {result} euros."
    else:
        name = _pick(rng, names_en)
        plural, _ = items_en[rng.randrange(len(items_en))]
        if kind == "give_buy":
            user = f"{name} has {a} {plural}, gives away {b}, then buys {c} more. How many {plural} does {name} have now?"
            result = a - b + c
            answer = f"Start with {a}, give away {b}: {a} - {b} = {a - b}. Buy {c}: {a - b} + {c} = {result}. {name} now has {result} {plural}."
        elif kind == "groups":
            groups = rng.randint(2, 9)
            each = rng.randint(2, 12)
            result = groups * each
            user = (
                f"{name} puts {plural} into {groups} boxes of {each}. How many {plural} are there in total?"
            )
            answer = f"{groups} boxes × {each} {plural} = {result}. There are {result} {plural} in total."
        else:
            price = rng.randint(2, 15)
            count = rng.randint(2, 9)
            result = price * count
            user = f"One item costs {price} dollars. How much do {count} items cost?"
            answer = f"{count} × {price} = {result}. The {count} items cost {result} dollars."
    return TaskExample("word_problem", lang, user, answer, {"type": "exact", "answer": str(result)})


# ----------------------------------------------------------------------------------------- listes et texte

WORDS_FR = (
    "maison",
    "ordinateur",
    "fenêtre",
    "jardin",
    "voiture",
    "soleil",
    "montagne",
    "rivière",
    "cuisine",
    "lumière",
    "chocolat",
    "musique",
    "bateau",
    "fromage",
    "nuage",
    "étoile",
    "chemin",
    "papier",
    "oiseau",
    "printemps",
    "chaise",
    "bureau",
    "clavier",
    "écran",
    "fichier",
    "dossier",
    "réseau",
    "serveur",
    "programme",
    "fonction",
    "variable",
    "tableau",
    "boucle",
    "erreur",
    "mémoire",
    "processeur",
    "logiciel",
    "données",
    "système",
    "projet",
    "école",
    "livre",
    "table",
    "porte",
    "ville",
    "forêt",
    "plage",
    "hiver",
    "automne",
    "été",
    "matin",
    "soir",
    "pomme",
    "orange",
    "citron",
    "fraise",
    "cerise",
    "poisson",
    "chat",
    "chien",
    "cheval",
    "lapin",
    "tortue",
)
WORDS_EN = (
    "house",
    "computer",
    "window",
    "garden",
    "car",
    "sun",
    "mountain",
    "river",
    "kitchen",
    "light",
    "chocolate",
    "music",
    "boat",
    "cheese",
    "cloud",
    "star",
    "path",
    "paper",
    "bird",
    "spring",
    "chair",
    "desk",
    "keyboard",
    "screen",
    "file",
    "folder",
    "network",
    "server",
    "program",
    "function",
    "variable",
    "array",
    "loop",
    "error",
    "memory",
    "processor",
    "software",
    "data",
    "system",
    "project",
    "school",
    "book",
    "table",
    "door",
    "city",
    "forest",
    "beach",
    "winter",
    "autumn",
    "summer",
    "morning",
    "evening",
    "apple",
    "orange",
    "lemon",
    "strawberry",
    "cherry",
    "fish",
    "cat",
    "dog",
    "horse",
    "rabbit",
    "turtle",
)


def list_ops(rng: random.Random, lang: str) -> TaskExample:
    numbers = [rng.randint(-20, 99) for _ in range(rng.randint(3, 8))]
    shown = ", ".join(map(str, numbers))
    kind = rng.choice(("sort", "sort_desc", "max", "min", "sum", "count", "even"))
    if kind == "sort":
        result = ", ".join(map(str, sorted(numbers)))
        user = (
            f"Trie ces nombres par ordre croissant : {shown}"
            if lang == "fr"
            else f"Sort these numbers in ascending order: {shown}"
        )
        answer = result
    elif kind == "sort_desc":
        result = ", ".join(map(str, sorted(numbers, reverse=True)))
        user = (
            f"Trie ces nombres par ordre décroissant : {shown}"
            if lang == "fr"
            else f"Sort these numbers in descending order: {shown}"
        )
        answer = result
    elif kind == "max":
        result = str(max(numbers))
        user = (
            f"Quel est le plus grand nombre parmi : {shown} ?"
            if lang == "fr"
            else f"What is the largest number among: {shown}?"
        )
        answer = f"Le plus grand est {result}." if lang == "fr" else f"The largest is {result}."
    elif kind == "min":
        result = str(min(numbers))
        user = (
            f"Quel est le plus petit nombre parmi : {shown} ?"
            if lang == "fr"
            else f"What is the smallest number among: {shown}?"
        )
        answer = f"Le plus petit est {result}." if lang == "fr" else f"The smallest is {result}."
    elif kind == "sum":
        result = str(sum(numbers))
        running, steps = 0, []
        for n in numbers:
            running += n
            steps.append(str(running))
        user = f"Additionne ces nombres : {shown}" if lang == "fr" else f"Add up these numbers: {shown}"
        answer = (
            f"Sommes successives : {', '.join(steps)}. Le total est {result}."
            if lang == "fr"
            else f"Running totals: {', '.join(steps)}. The total is {result}."
        )
    elif kind == "count":
        result = str(len(numbers))
        user = (
            f"Combien y a-t-il de nombres dans cette liste : {shown} ?"
            if lang == "fr"
            else f"How many numbers are in this list: {shown}?"
        )
        answer = f"Il y a {result} nombres." if lang == "fr" else f"There are {result} numbers."
    else:
        evens = [n for n in numbers if n % 2 == 0]
        result = ", ".join(map(str, evens)) if evens else ("aucun" if lang == "fr" else "no even numbers")
        user = (
            f"Quels sont les nombres pairs dans : {shown} ?"
            if lang == "fr"
            else f"Which numbers are even in: {shown}?"
        )
        answer = (
            (f"Les nombres pairs sont : {result}." if lang == "fr" else f"The even numbers are: {result}.")
            if evens
            else ("Il n'y a aucun nombre pair." if lang == "fr" else "There are no even numbers.")
        )
    return TaskExample("list_ops", lang, user, answer, {"type": "exact", "answer": result})


def text_ops(rng: random.Random, lang: str) -> TaskExample:
    word = _pick(rng, WORDS_FR if lang == "fr" else WORDS_EN)
    kind = rng.choice(("reverse", "length", "upper", "first", "last", "spell"))
    if kind == "reverse":
        result = word[::-1]
        user = (
            f"Écris le mot « {word} » à l'envers."
            if lang == "fr"
            else f'Write the word "{word}" backwards.'
        )
        answer = (
            f"{' '.join(word)} → à l'envers : {result}"
            if lang == "fr"
            else f"{' '.join(word)} → backwards: {result}"
        )
    elif kind == "length":
        result = str(len(word))
        user = (
            f"Combien de lettres contient le mot « {word} » ?"
            if lang == "fr"
            else f'How many letters are in the word "{word}"?'
        )
        answer = (
            f"{' '.join(word)} : le mot « {word} » contient {result} lettres."
            if lang == "fr"
            else f'{" ".join(word)}: the word "{word}" has {result} letters.'
        )
    elif kind == "upper":
        result = word.upper()
        user = (
            f"Mets ce mot en majuscules : {word}"
            if lang == "fr"
            else f"Convert this word to uppercase: {word}"
        )
        answer = result
    elif kind == "first":
        result = word[0]
        user = (
            f"Quelle est la première lettre du mot « {word} » ?"
            if lang == "fr"
            else f'What is the first letter of "{word}"?'
        )
        answer = (
            f"La première lettre est « {result} »." if lang == "fr" else f'The first letter is "{result}".'
        )
    elif kind == "last":
        result = word[-1]
        user = (
            f"Quelle est la dernière lettre du mot « {word} » ?"
            if lang == "fr"
            else f'What is the last letter of "{word}"?'
        )
        answer = (
            f"La dernière lettre est « {result} »." if lang == "fr" else f'The last letter is "{result}".'
        )
    else:
        result = "-".join(word)
        user = (
            f"Épelle le mot « {word} » en séparant les lettres par des tirets."
            if lang == "fr"
            else f'Spell "{word}" with dashes between letters.'
        )
        answer = result
    return TaskExample("text_ops", lang, user, answer, {"type": "exact", "answer": result})


# ------------------------------------------------------------------------------------------ conversions

_UNITS = (
    ("km", "m", 1000, "kilomètres", "mètres", "kilometers", "meters"),
    ("m", "cm", 100, "mètres", "centimètres", "meters", "centimeters"),
    ("kg", "g", 1000, "kilogrammes", "grammes", "kilograms", "grams"),
    ("h", "min", 60, "heures", "minutes", "hours", "minutes"),
    ("min", "s", 60, "minutes", "secondes", "minutes", "seconds"),
    ("L", "mL", 1000, "litres", "millilitres", "liters", "milliliters"),
    ("Go", "Mo", 1024, "gigaoctets", "mégaoctets", "gigabytes", "megabytes"),
)


def conversions(rng: random.Random, lang: str) -> TaskExample:
    big, small, factor, big_fr, small_fr, big_en, small_en = _UNITS[rng.randrange(len(_UNITS))]
    value = rng.randint(1, 50)
    if rng.random() < 0.5:
        result = value * factor
        user = (
            f"Combien de {small_fr} y a-t-il dans {value} {big_fr} ?"
            if lang == "fr"
            else f"How many {small_en} are in {value} {big_en}?"
        )
        answer = (
            f"1 {big} = {factor} {small}, donc {value} {big} = {value} × {factor} = {result} {small}."
            if lang == "fr"
            else f"1 {big} = {factor} {small}, so {value} {big} = {value} × {factor} = {result} {small}."
        )
    else:
        result = value
        total = value * factor
        user = (
            f"Convertis {total} {small_fr} en {big_fr}."
            if lang == "fr"
            else f"Convert {total} {small_en} to {big_en}."
        )
        answer = (
            f"{total} ÷ {factor} = {result}, donc {total} {small} = {result} {big}."
            if lang == "fr"
            else f"{total} ÷ {factor} = {result}, so {total} {small} = {result} {big}."
        )
    return TaskExample("conversion", lang, user, answer, {"type": "exact", "answer": str(result)})


_DAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_DAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def dates(rng: random.Random, lang: str) -> TaskExample:
    start = dt.date(2000, 1, 1) + dt.timedelta(days=rng.randint(0, 365 * 30))
    kind = rng.choice(("weekday", "add_days", "between", "leap"))

    def show(d: dt.date) -> str:
        return (
            f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}"
            if lang == "fr"
            else f"{_MONTHS_EN[d.month - 1]} {d.day}, {d.year}"
        )

    if kind == "weekday":
        result = (_DAYS_FR if lang == "fr" else _DAYS_EN)[start.weekday()]
        user = (
            f"Quel jour de la semaine était le {show(start)} ?"
            if lang == "fr"
            else f"What day of the week was {show(start)}?"
        )
        answer = (
            f"Le {show(start)} était un {result}." if lang == "fr" else f"{show(start)} was a {result}."
        )
    elif kind == "add_days":
        days = rng.randint(1, 120)
        end = start + dt.timedelta(days=days)
        result = show(end)
        user = (
            f"Quelle date sera-t-il {days} jours après le {show(start)} ?"
            if lang == "fr"
            else f"What date is {days} days after {show(start)}?"
        )
        answer = (
            f"{days} jours après le {show(start)}, nous serons le {result}."
            if lang == "fr"
            else f"{days} days after {show(start)} is {result}."
        )
    elif kind == "between":
        end = start + dt.timedelta(days=rng.randint(1, 400))
        result = str((end - start).days)
        user = (
            f"Combien de jours y a-t-il entre le {show(start)} et le {show(end)} ?"
            if lang == "fr"
            else f"How many days are there between {show(start)} and {show(end)}?"
        )
        answer = f"Il y a {result} jours." if lang == "fr" else f"There are {result} days."
    else:
        year = rng.randint(1890, 2100)
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        result = ("oui" if leap else "non") if lang == "fr" else ("yes" if leap else "no")
        rule = (
            "divisible par 4, et pas par 100 sauf si divisible par 400"
            if lang == "fr"
            else "divisible by 4, and not by 100 unless divisible by 400"
        )
        user = f"L'année {year} est-elle bissextile ?" if lang == "fr" else f"Is {year} a leap year?"
        answer = (
            f"{'Oui' if leap else 'Non'} : une année est bissextile si elle est {rule}. {year} "
            f"{'remplit' if leap else 'ne remplit pas'} cette règle."
            if lang == "fr"
            else f"{'Yes' if leap else 'No'}: a year is a leap year if it is {rule}. {year} "
            f"{'meets' if leap else 'does not meet'} this rule."
        )
    return TaskExample("dates", lang, user, answer, {"type": "exact", "answer": result})


def logic(rng: random.Random, lang: str) -> TaskExample:
    kind = rng.choice(("bool", "compare", "syllogism"))
    if kind == "bool":
        values = [rng.choice((True, False)) for _ in range(3)]
        ops = [rng.choice(("and", "or")) for _ in range(2)]
        negate = rng.random() < 0.4
        expression = f"{'not ' if negate else ''}{values[0]} {ops[0]} {values[1]} {ops[1]} {values[2]}"
        result = str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307 - expression construite ici
        user = (
            f"Que vaut l'expression Python `{expression}` ?"
            if lang == "fr"
            else f"What does the Python expression `{expression}` evaluate to?"
        )
        answer = (
            f"`not` s'applique d'abord, puis `and`, puis `or`. Le résultat est {result}."
            if lang == "fr"
            else f"`not` binds first, then `and`, then `or`. The result is {result}."
        )
    elif kind == "compare":
        a, b = rng.randint(-50, 200), rng.randint(-50, 200)
        op = rng.choice(("<", ">", "<=", ">=", "==", "!="))
        truth = {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b, "==": a == b, "!=": a != b}[op]
        result = ("vrai" if truth else "faux") if lang == "fr" else ("true" if truth else "false")
        user = f"Est-ce que {a} {op} {b} est vrai ?" if lang == "fr" else f"Is {a} {op} {b} true?"
        answer = f"C'est {result}." if lang == "fr" else f"It is {result}."
    else:
        groups_fr = (
            ("chats", "mammifères"),
            ("roses", "fleurs"),
            ("carrés", "rectangles"),
            ("pythons", "serpents"),
        )
        groups_en = (
            ("cats", "mammals"),
            ("roses", "flowers"),
            ("squares", "rectangles"),
            ("pythons", "snakes"),
        )
        a, b = (groups_fr if lang == "fr" else groups_en)[rng.randrange(4)]
        name = _pick(rng, ("Tom", "Rex", "Zoé", "Max") if lang == "fr" else ("Tom", "Rex", "Zoe", "Max"))
        valid = rng.random() < 0.5
        if lang == "fr":
            user = (
                f"Tous les {a} sont des {b}. {name} est un des {a}. {name} est-il un des {b} ?"
                if valid
                else f"Tous les {a} sont des {b}. {name} est un des {b}. {name} est-il forcément un des {a} ?"
            )
            result = "oui" if valid else "non"
            answer = (
                "Oui : tout élément des " + a + " est un des " + b + "."
                if valid
                else f"Non, pas forcément : tous les {a} sont des {b}, mais tous les {b} ne sont pas des {a}."
            )
        else:
            user = (
                f"All {a} are {b}. {name} is one of the {a}. Is {name} one of the {b}?"
                if valid
                else f"All {a} are {b}. {name} is one of the {b}. Is {name} necessarily one of the {a}?"
            )
            result = "yes" if valid else "no"
            answer = (
                f"Yes: every one of the {a} is one of the {b}."
                if valid
                else f"No, not necessarily: all {a} are {b}, but not all {b} are {a}."
            )
    return TaskExample("logic", lang, user, answer, {"type": "exact", "answer": result})


def json_format(rng: random.Random, lang: str) -> TaskExample:
    name = _pick(rng, ("Paul", "Alice", "Karim", "Sofia", "Jean", "Mia", "Omar", "Lina"))
    age = rng.randint(18, 90)
    city = _pick(rng, ("Paris", "Lyon", "Lille", "Nice", "Nantes", "Bordeaux", "Toulouse", "Rennes"))
    keys = ("nom", "age", "ville") if lang == "fr" else ("name", "age", "city")
    obj = {keys[0]: name, keys[1]: age, keys[2]: city}
    user = (
        f"Donne un objet JSON avec les clés {keys[0]}, {keys[1]} et {keys[2]} pour {name}, {age} ans, qui habite à {city}."
        if lang == "fr"
        else f"Give a JSON object with keys {keys[0]}, {keys[1]} and {keys[2]} for {name}, aged {age}, living in {city}."
    )
    answer = "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"
    return TaskExample("json", lang, user, answer, {"type": "json", "answer": obj})


# ------------------------------------------------------------------------------------------------ code


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    params: str
    fr: str
    en: str
    body: str
    tests: tuple[tuple[Any, ...], ...]

    def code(self) -> str:
        return f"def {self.name}({self.params}):\n{self.body}"

    def run(self, *args: Any) -> Any:
        namespace: dict[str, Any] = {}
        exec(self.code(), namespace)  # noqa: S102 - code écrit par l'équipe d'APEX
        return namespace[self.name](*args)


def _spec(name: str, params: str, fr: str, en: str, body: str, *tests: tuple[Any, ...]) -> FunctionSpec:
    return FunctionSpec(name, params, fr, en, body, tests)


FUNCTIONS: tuple[FunctionSpec, ...] = (
    _spec(
        "est_pair",
        "n",
        "renvoie True si l'entier n est pair, sinon False",
        "returns True if the integer n is even, otherwise False",
        "    return n % 2 == 0\n",
        (4,),
        (7,),
        (0,),
        (-3,),
    ),
    _spec(
        "carre",
        "x",
        "renvoie le carré de x",
        "returns the square of x",
        "    return x * x\n",
        (3,),
        (-4,),
        (0,),
        (12,),
    ),
    _spec(
        "somme_liste",
        "nombres",
        "renvoie la somme des éléments de la liste nombres",
        "returns the sum of the elements of the list nombres",
        "    total = 0\n    for n in nombres:\n        total += n\n    return total\n",
        ([1, 2, 3],),
        ([],),
        ([-5, 5, 10],),
    ),
    _spec(
        "maximum",
        "nombres",
        "renvoie le plus grand élément d'une liste non vide",
        "returns the largest element of a non-empty list",
        "    plus_grand = nombres[0]\n    for n in nombres[1:]:\n        if n > plus_grand:\n            plus_grand = n\n    return plus_grand\n",
        ([3, 9, 2],),
        ([-1, -7],),
        ([5],),
    ),
    _spec(
        "minimum",
        "nombres",
        "renvoie le plus petit élément d'une liste non vide",
        "returns the smallest element of a non-empty list",
        "    plus_petit = nombres[0]\n    for n in nombres[1:]:\n        if n < plus_petit:\n            plus_petit = n\n    return plus_petit\n",
        ([3, 9, 2],),
        ([-1, -7],),
        ([5],),
    ),
    _spec(
        "inverser_chaine",
        "texte",
        "renvoie la chaîne texte à l'envers",
        "returns the string texte reversed",
        "    return texte[::-1]\n",
        ("abc",),
        ("",),
        ("radar",),
        ("Python",),
    ),
    _spec(
        "compter_voyelles",
        "texte",
        "compte les voyelles (a, e, i, o, u, y) d'une chaîne en minuscules",
        "counts the vowels (a, e, i, o, u, y) in a lowercase string",
        '    return sum(1 for c in texte if c in "aeiouy")\n',
        ("bonjour",),
        ("rythme",),
        ("",),
        ("python",),
    ),
    _spec(
        "factorielle",
        "n",
        "renvoie la factorielle de l'entier n >= 0",
        "returns the factorial of the integer n >= 0",
        "    resultat = 1\n    for i in range(2, n + 1):\n        resultat *= i\n    return resultat\n",
        (0,),
        (1,),
        (5,),
        (10,),
    ),
    _spec(
        "fibonacci",
        "n",
        "renvoie le n-ième nombre de Fibonacci (fibonacci(0) = 0, fibonacci(1) = 1)",
        "returns the n-th Fibonacci number (fibonacci(0) = 0, fibonacci(1) = 1)",
        "    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
        (0,),
        (1,),
        (2,),
        (10,),
        (20,),
    ),
    _spec(
        "est_premier",
        "n",
        "renvoie True si n est un nombre premier",
        "returns True if n is a prime number",
        "    if n < 2:\n        return False\n    i = 2\n    while i * i <= n:\n        if n % i == 0:\n            return False\n        i += 1\n    return True\n",
        (1,),
        (2,),
        (9,),
        (13,),
        (97,),
        (100,),
    ),
    _spec(
        "est_palindrome",
        "texte",
        "renvoie True si la chaîne se lit de la même façon dans les deux sens",
        "returns True if the string reads the same in both directions",
        "    return texte == texte[::-1]\n",
        ("radar",),
        ("python",),
        ("",),
        ("abba",),
    ),
    _spec(
        "moyenne",
        "nombres",
        "renvoie la moyenne d'une liste non vide de nombres",
        "returns the average of a non-empty list of numbers",
        "    return sum(nombres) / len(nombres)\n",
        ([1, 2, 3],),
        ([10],),
        ([2, 4],),
    ),
    _spec(
        "filtrer_pairs",
        "nombres",
        "renvoie la liste des nombres pairs de la liste nombres, dans le même ordre",
        "returns the list of even numbers from the list nombres, in the same order",
        "    return [n for n in nombres if n % 2 == 0]\n",
        ([1, 2, 3, 4],),
        ([],),
        ([7, 9],),
    ),
    _spec(
        "doubler",
        "nombres",
        "renvoie une nouvelle liste où chaque nombre est multiplié par 2",
        "returns a new list where each number is multiplied by 2",
        "    return [n * 2 for n in nombres]\n",
        ([1, 2, 3],),
        ([],),
        ([-1, 0],),
    ),
    _spec(
        "compter_mots",
        "phrase",
        "renvoie le nombre de mots d'une phrase (séparés par des espaces)",
        "returns the number of words in a sentence (separated by spaces)",
        "    return len(phrase.split())\n",
        ("le chat dort",),
        ("",),
        ("un",),
        ("  deux   mots ",),
    ),
    _spec(
        "majuscules",
        "texte",
        "renvoie la chaîne texte en majuscules",
        "returns the string texte in uppercase",
        "    return texte.upper()\n",
        ("abc",),
        ("Python 3",),
        ("",),
    ),
    _spec(
        "valeur_absolue",
        "x",
        "renvoie la valeur absolue de x sans utiliser abs",
        "returns the absolute value of x without using abs",
        "    if x < 0:\n        return -x\n    return x\n",
        (-5,),
        (3,),
        (0,),
    ),
    _spec(
        "pgcd",
        "a, b",
        "renvoie le plus grand commun diviseur de deux entiers positifs a et b",
        "returns the greatest common divisor of two positive integers a and b",
        "    while b:\n        a, b = b, a % b\n    return a\n",
        (12, 18),
        (7, 5),
        (100, 25),
    ),
    _spec(
        "puissance",
        "base, exposant",
        "renvoie base élevé à la puissance exposant (exposant entier >= 0)",
        "returns base raised to the power exposant (integer exposant >= 0)",
        "    resultat = 1\n    for _ in range(exposant):\n        resultat *= base\n    return resultat\n",
        (2, 10),
        (5, 0),
        (3, 3),
    ),
    _spec(
        "dernier_element",
        "liste",
        "renvoie le dernier élément d'une liste non vide",
        "returns the last element of a non-empty list",
        "    return liste[-1]\n",
        ([1, 2, 3],),
        (["a"],),
    ),
    _spec(
        "premiers_n",
        "liste, n",
        "renvoie les n premiers éléments de la liste",
        "returns the first n elements of the list",
        "    return liste[:n]\n",
        ([1, 2, 3, 4], 2),
        ([1], 3),
        ([], 1),
    ),
    _spec(
        "supprimer_doublons",
        "liste",
        "renvoie la liste sans doublons en gardant l'ordre de première apparition",
        "returns the list without duplicates, keeping the order of first appearance",
        "    vus = set()\n    resultat = []\n    for x in liste:\n        if x not in vus:\n            vus.add(x)\n            resultat.append(x)\n    return resultat\n",
        ([1, 2, 1, 3, 2],),
        ([],),
        (["a", "a"],),
    ),
    _spec(
        "compter_occurrences",
        "liste, valeur",
        "compte combien de fois valeur apparaît dans liste",
        "counts how many times valeur appears in liste",
        "    compte = 0\n    for x in liste:\n        if x == valeur:\n            compte += 1\n    return compte\n",
        ([1, 2, 1, 1], 1),
        ([], 3),
        (["a", "b"], "c"),
    ),
    _spec(
        "celsius_vers_fahrenheit",
        "celsius",
        "convertit une température de degrés Celsius en degrés Fahrenheit",
        "converts a temperature from degrees Celsius to degrees Fahrenheit",
        "    return celsius * 9 / 5 + 32\n",
        (0,),
        (100,),
        (-40,),
        (37,),
    ),
    _spec(
        "somme_chiffres",
        "n",
        "renvoie la somme des chiffres d'un entier n >= 0",
        "returns the sum of the digits of an integer n >= 0",
        "    total = 0\n    while n > 0:\n        total += n % 10\n        n //= 10\n    return total\n",
        (0,),
        (123,),
        (9999,),
    ),
    _spec(
        "inverser_liste",
        "liste",
        "renvoie une nouvelle liste avec les éléments dans l'ordre inverse",
        "returns a new list with the elements in reverse order",
        "    return liste[::-1]\n",
        ([1, 2, 3],),
        ([],),
    ),
    _spec(
        "est_trie",
        "liste",
        "renvoie True si la liste est triée par ordre croissant",
        "returns True if the list is sorted in ascending order",
        "    for i in range(len(liste) - 1):\n        if liste[i] > liste[i + 1]:\n            return False\n    return True\n",
        ([1, 2, 2, 5],),
        ([3, 1],),
        ([],),
    ),
    _spec(
        "fusionner_dicts",
        "a, b",
        "renvoie un nouveau dictionnaire contenant a puis b (les clés de b l'emportent)",
        "returns a new dictionary containing a then b (keys of b win)",
        "    resultat = dict(a)\n    resultat.update(b)\n    return resultat\n",
        ({"x": 1}, {"y": 2}),
        ({"x": 1}, {"x": 3}),
    ),
    _spec(
        "compter_lettres",
        "texte",
        "renvoie un dictionnaire lettre -> nombre d'apparitions dans texte",
        "returns a dictionary letter -> number of occurrences in texte",
        "    compte = {}\n    for c in texte:\n        compte[c] = compte.get(c, 0) + 1\n    return compte\n",
        ("abca",),
        ("",),
    ),
    _spec(
        "aplatir",
        "listes",
        "aplatit une liste de listes en une seule liste",
        "flattens a list of lists into a single list",
        "    return [x for sous_liste in listes for x in sous_liste]\n",
        ([[1, 2], [3]],),
        ([],),
        ([[], [4]],),
    ),
    _spec(
        "produit_liste",
        "nombres",
        "renvoie le produit des éléments de la liste (1 pour une liste vide)",
        "returns the product of the elements of the list (1 for an empty list)",
        "    produit = 1\n    for n in nombres:\n        produit *= n\n    return produit\n",
        ([2, 3, 4],),
        ([],),
        ([5, 0],),
    ),
    _spec(
        "nombre_positifs",
        "nombres",
        "compte les nombres strictement positifs de la liste",
        "counts the strictly positive numbers in the list",
        "    return sum(1 for n in nombres if n > 0)\n",
        ([1, -2, 3, 0],),
        ([],),
    ),
    _spec(
        "capitaliser_mots",
        "phrase",
        "met en majuscule la première lettre de chaque mot (mots séparés par un espace)",
        "capitalizes the first letter of each word (words separated by one space)",
        '    return " ".join(mot[:1].upper() + mot[1:] for mot in phrase.split(" "))\n',
        ("le chat noir",),
        ("a",),
    ),
    _spec(
        "table_multiplication",
        "n",
        "renvoie la liste [n*1, n*2, ..., n*10]",
        "returns the list [n*1, n*2, ..., n*10]",
        "    return [n * i for i in range(1, 11)]\n",
        (3,),
        (0,),
    ),
    _spec(
        "diviseurs",
        "n",
        "renvoie la liste croissante des diviseurs positifs de n > 0",
        "returns the ascending list of positive divisors of n > 0",
        "    return [d for d in range(1, n + 1) if n % d == 0]\n",
        (12,),
        (1,),
        (13,),
    ),
    _spec(
        "indice_de",
        "liste, valeur",
        "renvoie l'indice de la première occurrence de valeur dans liste, ou -1",
        "returns the index of the first occurrence of valeur in liste, or -1",
        "    for i, x in enumerate(liste):\n        if x == valeur:\n            return i\n    return -1\n",
        ([5, 3, 5], 5),
        ([1, 2], 9),
        ([], 1),
    ),
    _spec(
        "binaire",
        "n",
        "renvoie l'écriture binaire (chaîne) d'un entier n >= 0",
        "returns the binary representation (string) of an integer n >= 0",
        '    if n == 0:\n        return "0"\n    chiffres = ""\n    while n > 0:\n        chiffres = str(n % 2) + chiffres\n        n //= 2\n    return chiffres\n',
        (0,),
        (5,),
        (255,),
    ),
    _spec(
        "tronquer",
        "texte, longueur",
        'coupe texte à longueur caractères et ajoute "..." s\'il était plus long',
        'cuts texte to longueur characters and adds "..." if it was longer',
        '    if len(texte) <= longueur:\n        return texte\n    return texte[:longueur] + "..."\n',
        ("bonjour", 3),
        ("abc", 5),
    ),
    _spec(
        "mention",
        "note",
        'renvoie "refusé" si note < 10, "passable" si note < 12, "bien" si note < 16, sinon "très bien"',
        'returns "refusé" if note < 10, "passable" if note < 12, "bien" if note < 16, otherwise "très bien"',
        '    if note < 10:\n        return "refusé"\n    if note < 12:\n        return "passable"\n    if note < 16:\n        return "bien"\n    return "très bien"\n',
        (8,),
        (10,),
        (12,),
        (15,),
        (18,),
    ),
    _spec(
        "somme_carres",
        "n",
        "renvoie 1² + 2² + ... + n²",
        "returns 1² + 2² + ... + n²",
        "    return sum(i * i for i in range(1, n + 1))\n",
        (0,),
        (3,),
        (10,),
    ),
    _spec(
        "multiples_de",
        "n, limite",
        "renvoie la liste des multiples de n strictement inférieurs à limite (n > 0, en commençant par 0)",
        "returns the list of multiples of n strictly below limite (n > 0, starting at 0)",
        "    return list(range(0, limite, n))\n",
        (3, 10),
        (5, 5),
    ),
    _spec(
        "remplacer_espaces",
        "texte",
        "remplace chaque espace de texte par un tiret bas",
        "replaces each space in texte with an underscore",
        '    return texte.replace(" ", "_")\n',
        ("a b c",),
        ("abc",),
    ),
    _spec(
        "nombre_majuscules",
        "texte",
        "compte les lettres majuscules de texte",
        "counts the uppercase letters in texte",
        "    return sum(1 for c in texte if c.isupper())\n",
        ("Bonjour Paris",),
        ("abc",),
    ),
    _spec(
        "deuxieme_plus_grand",
        "nombres",
        "renvoie la deuxième plus grande valeur distincte d'une liste (au moins deux valeurs distinctes)",
        "returns the second largest distinct value of a list (at least two distinct values)",
        "    valeurs = sorted(set(nombres))\n    return valeurs[-2]\n",
        ([1, 5, 3],),
        ([7, 7, 2],),
    ),
    _spec(
        "rotation_gauche",
        "liste, k",
        "décale les éléments de liste de k positions vers la gauche (circulairement)",
        "rotates the elements of liste k positions to the left (circularly)",
        "    if not liste:\n        return []\n    k %= len(liste)\n    return liste[k:] + liste[:k]\n",
        ([1, 2, 3, 4], 1),
        ([1, 2, 3], 4),
        ([], 2),
    ),
    _spec(
        "anagrammes",
        "a, b",
        "renvoie True si les chaînes a et b sont des anagrammes l'une de l'autre",
        "returns True if strings a and b are anagrams of each other",
        "    return sorted(a) == sorted(b)\n",
        ("chien", "niche"),
        ("abc", "abd"),
    ),
    _spec(
        "compter_pairs_impairs",
        "nombres",
        "renvoie un tuple (nombre de pairs, nombre d'impairs)",
        "returns a tuple (number of even values, number of odd values)",
        "    pairs = sum(1 for n in nombres if n % 2 == 0)\n    return (pairs, len(nombres) - pairs)\n",
        ([1, 2, 3],),
        ([],),
    ),
    _spec(
        "initiales",
        "nom_complet",
        "renvoie les initiales en majuscules d'un nom complet (mots séparés par des espaces)",
        "returns the uppercase initials of a full name (words separated by spaces)",
        '    return "".join(mot[0].upper() for mot in nom_complet.split())\n',
        ("jean paul sartre",),
        ("Ada Lovelace",),
    ),
    _spec(
        "clamp",
        "x, bas, haut",
        "ramène x dans l'intervalle [bas, haut]",
        "clamps x into the interval [bas, haut]",
        "    if x < bas:\n        return bas\n    if x > haut:\n        return haut\n    return x\n",
        (5, 0, 10),
        (-3, 0, 10),
        (12, 0, 10),
    ),
    _spec(
        "longueur_max",
        "mots",
        "renvoie la longueur du mot le plus long d'une liste (0 si vide)",
        "returns the length of the longest word in a list (0 if empty)",
        "    return max((len(m) for m in mots), default=0)\n",
        (["a", "abc", "ab"],),
        ([],),
    ),
)
#: Fonctions JAMAIS vues à l'entraînement : mesurent la généralisation (pas la mémoire).
HELDOUT_FUNCTIONS = frozenset(
    {
        "somme_carres",
        "anagrammes",
        "clamp",
        "initiales",
        "rotation_gauche",
        "compter_pairs_impairs",
        "longueur_max",
        "multiples_de",
        "nombre_majuscules",
        "deuxieme_plus_grand",
    }
)


def _function_tests(spec: FunctionSpec) -> list[list[Any]]:
    return [[list(args), spec.run(*args)] for args in spec.tests]


def write_function(rng: random.Random, lang: str, spec: FunctionSpec) -> TaskExample:
    description = spec.fr if lang == "fr" else spec.en
    signature = f"{spec.name}({spec.params})"
    if lang == "fr":
        user = _pick(
            rng,
            (
                f"Écris une fonction Python `{signature}` qui {description}.",
                f"Écris en Python la fonction {signature} : elle {description}.",
                f"J'ai besoin d'une fonction Python nommée `{spec.name}` qui {description}. Paramètres : {spec.params}.",
            ),
        )
        answer = f"```python\n{spec.code()}```\n" + _pick(
            rng, ("", f"La fonction `{spec.name}` {description}.")
        )
    else:
        user = _pick(
            rng,
            (
                f"Write a Python function `{signature}` that {description}.",
                f"Write the Python function {signature}: it {description}.",
                f"I need a Python function named `{spec.name}` that {description}. Parameters: {spec.params}.",
            ),
        )
        answer = f"```python\n{spec.code()}```\n" + _pick(
            rng, ("", f"The function `{spec.name}` {description}.")
        )
    return TaskExample(
        "code_write",
        lang,
        user,
        answer.strip(),
        {"type": "code", "function": spec.name, "tests": _function_tests(spec)},
        meta={"function": spec.name, "heldout": spec.name in HELDOUT_FUNCTIONS},
    )


_MUTATIONS = (
    (" + ", " - "),
    (" - ", " + "),
    (" < ", " <= "),
    (" > ", " >= "),
    (" <= ", " < "),
    (" >= ", " > "),
    (" == ", " != "),
    ("range(2, n + 1)", "range(2, n)"),
    ("[::-1]", "[::1]"),
    (" * ", " + "),
    ("% 2 == 0", "% 2 == 1"),
    ("return True", "return False"),
    ("[-1]", "[0]"),
    ("+= 1", "+= 2"),
)


_MUTATION_CACHE: dict[str, tuple[tuple[str, str, str], ...]] = {}


class _StepLimit(Exception):
    pass


def _passes_bounded(code: str, name: str, tests: list[list[Any]], max_lines: int = 200_000) -> bool:
    """Le code (écrit ou muté par APEX, jamais d'origine extérieure) passe-t-il les tests ? Exécuté dans ce
    processus avec un plafond d'instructions exécutées (une mutation peut créer une boucle infinie)."""
    import sys

    namespace: dict[str, Any] = {}
    counter = [0]

    def tracer(frame: Any, event: str, arg: Any) -> Any:
        if event == "line":
            counter[0] += 1
            if counter[0] > max_lines:
                raise _StepLimit
        return tracer

    try:
        exec(code, namespace)  # noqa: S102 - code de l'équipe d'APEX ou sa mutation
        function = namespace[name]
        previous = sys.gettrace()
        sys.settrace(tracer)
        try:
            for args, expected in tests:
                got = function(*args)
                if (list(got) if isinstance(got, tuple) else got) != (
                    list(expected) if isinstance(expected, tuple) else expected
                ):
                    return False
        finally:
            sys.settrace(previous)
    except Exception:  # erreur, plafond atteint : la mutation casse bien la fonction
        return False
    return True


def valid_mutations(spec: FunctionSpec) -> tuple[tuple[str, str, str], ...]:
    """Mutations qui cassent VRAIMENT la fonction (au moins un test échoue), avec un plafond d'instructions
    (une mutation peut créer une boucle infinie). (avant, après, code cassé)."""
    if spec.name in _MUTATION_CACHE:
        return _MUTATION_CACHE[spec.name]
    code = spec.code()
    expected = _function_tests(spec)
    found = []
    for wrong_from, wrong_to in _MUTATIONS:
        if wrong_from not in code:
            continue
        position = code.index(wrong_from)
        broken = code[:position] + code[position:].replace(wrong_from, wrong_to, 1)
        if not _passes_bounded(broken, spec.name, expected):
            found.append((wrong_from, wrong_to, broken))
    _MUTATION_CACHE[spec.name] = tuple(found)
    return _MUTATION_CACHE[spec.name]


def fix_bug(rng: random.Random, lang: str, spec: FunctionSpec) -> TaskExample | None:
    mutations = valid_mutations(spec)
    if not mutations:
        return None
    wrong_from, wrong_to, broken = mutations[rng.randrange(len(mutations))]
    code = spec.code()
    line = next(
        after
        for before, after in zip(code.splitlines(), broken.splitlines(), strict=True)
        if before != after
    )
    description = spec.fr if lang == "fr" else spec.en
    if lang == "fr":
        user = (
            f"Cette fonction devrait faire ceci : elle {description}. Elle contient un bug. Corrige-la.\n"
            f"```python\n{broken}```"
        )
        answer = (
            f"Le bug est sur la ligne `{line.strip()}` : `{wrong_to.strip()}` doit être "
            f"`{wrong_from.strip()}`.\n```python\n{code}```"
        )
    else:
        user = f"This function should do the following: it {description}. It has a bug. Fix it.\n```python\n{broken}```"
        answer = (
            f"The bug is on the line `{line.strip()}`: `{wrong_to.strip()}` should be "
            f"`{wrong_from.strip()}`.\n```python\n{code}```"
        )
    return TaskExample(
        "code_fix",
        lang,
        user,
        answer,
        {"type": "code", "function": spec.name, "tests": _function_tests(spec)},
        meta={"function": spec.name, "heldout": spec.name in HELDOUT_FUNCTIONS},
    )


def _safe_call(function: Callable[..., Any], args: list[Any]) -> Any:
    try:
        return function(*args)
    except Exception as exc:
        return f"<{exc.__class__.__name__}>"


def explain_function(rng: random.Random, lang: str, spec: FunctionSpec) -> TaskExample:
    if lang == "fr":
        user = (
            _pick(rng, ("Que fait cette fonction ?", "Explique ce que fait ce code."))
            + f"\n```python\n{spec.code()}```"
        )
        answer = f"La fonction `{spec.name}` {spec.fr}."
    else:
        user = (
            _pick(rng, ("What does this function do?", "Explain what this code does."))
            + f"\n```python\n{spec.code()}```"
        )
        answer = f"The function `{spec.name}` {spec.en}."
    return TaskExample(
        "code_explain",
        lang,
        user,
        answer,
        {"type": "none"},
        meta={"function": spec.name, "heldout": spec.name in HELDOUT_FUNCTIONS},
    )


def _program(rng: random.Random) -> str:
    kind = rng.choice(("sum_range", "loop_if", "string", "list_comp", "while", "dict", "nested", "func"))
    a, b = rng.randint(0, 5), rng.randint(6, 15)
    if kind == "sum_range":
        return f"total = 0\nfor i in range({a}, {b}):\n    total += i\nprint(total)\n"
    if kind == "loop_if":
        m = rng.randint(2, 4)
        return f"for i in range({a}, {b}):\n    if i % {m} == 0:\n        print(i)\n"
    if kind == "string":
        word = rng.choice(WORDS_EN)
        k = rng.randint(1, 3)
        return f'mot = "{word}"\nprint(mot.upper())\nprint(len(mot))\nprint(mot[:{k}] * 2)\n'
    if kind == "list_comp":
        m = rng.randint(2, 3)
        return f"valeurs = [x * {m} for x in range({a}, {a + rng.randint(3, 6)})]\nprint(valeurs)\nprint(sum(valeurs))\n"
    if kind == "while":
        n = rng.randint(20, 200)
        return f"n = {n}\netapes = 0\nwhile n > 1:\n    n = n // 2\n    etapes += 1\nprint(etapes)\n"
    if kind == "dict":
        word = rng.choice(WORDS_EN)
        return f'compte = {{}}\nfor c in "{word}":\n    compte[c] = compte.get(c, 0) + 1\nprint(compte)\n'
    if kind == "nested":
        n = rng.randint(2, 4)
        return f'for i in range(1, {n + 1}):\n    print("*" * i)\n'
    m = rng.randint(2, 9)
    return f"def f(x, y={m}):\n    return x * y + 1\n\nprint(f({a}))\nprint(f({a}, {b}))\n"


def program_output(rng: random.Random, lang: str) -> TaskExample:
    program = _program(rng)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(program, {"__builtins__": {"print": print, "range": range, "len": len, "sum": sum}})  # noqa: S102
    output = buffer.getvalue().rstrip("\n")
    user = (
        f"Qu'affiche ce programme Python ?\n```python\n{program}```"
        if lang == "fr"
        else f"What does this Python program print?\n```python\n{program}```"
    )
    answer = f"Il affiche :\n```\n{output}\n```" if lang == "fr" else f"It prints:\n```\n{output}\n```"
    return TaskExample("program_output", lang, user, answer, {"type": "exact", "answer": output})


# ---------------------------------------------------------------------------------- honnêteté et identité

_REALTIME_FR = (
    "Quel temps fait-il à {city} aujourd'hui ?",
    "Quelles sont les actualités du jour ?",
    "Quel est le cours actuel du bitcoin ?",
    "Quelle heure est-il en ce moment ?",
    "Qui a gagné le match d'hier soir ?",
    "Quel est le prix de l'essence aujourd'hui à {city} ?",
    "Peux-tu vérifier si le site {site} fonctionne en ce moment ?",
    "Quel est le dernier tweet de {name} ?",
)
_REALTIME_EN = (
    "What is the weather in {city} today?",
    "What are today's news headlines?",
    "What is the current price of bitcoin?",
    "What time is it right now?",
    "Who won last night's game?",
    "What is the price of gas in {city} today?",
    "Can you check whether the website {site} is up right now?",
    "What is {name}'s latest post?",
)
_ACTIONS_FR = (
    "As-tu envoyé l'e-mail à {name} ?",
    "Tu as bien sauvegardé mon fichier ?",
    "As-tu lancé les tests ?",
    "Peux-tu confirmer que tu as supprimé le dossier temporaire ?",
    "Tu as installé la bibliothèque ?",
    "As-tu redémarré le serveur ?",
)
_ACTIONS_EN = (
    "Did you send the email to {name}?",
    "Did you save my file?",
    "Did you run the tests?",
    "Can you confirm you deleted the temporary folder?",
    "Did you install the library?",
    "Did you restart the server?",
)
_MISSING_FR = (
    "Corrige mon code.",
    "Pourquoi mon programme plante ?",
    "Traduis ce texte.",
    "Résume le document.",
    "Quelle est la valeur de x ?",
    "Est-ce que ma fonction est correcte ?",
)
_MISSING_EN = (
    "Fix my code.",
    "Why does my program crash?",
    "Translate this text.",
    "Summarize the document.",
    "What is the value of x?",
    "Is my function correct?",
)
_PRIVATE_FR = (
    "Quel est mon nom ?",
    "Où est-ce que j'habite ?",
    "Quel âge ai-je ?",
    "Quel est le numéro de téléphone de {name} ?",
    "Quel est le mot de passe de mon compte ?",
)
_PRIVATE_EN = (
    "What is my name?",
    "Where do I live?",
    "How old am I?",
    "What is {name}'s phone number?",
    "What is my account password?",
)


def honesty(rng: random.Random, lang: str) -> TaskExample:
    kind = rng.choice(("realtime", "action", "missing", "private"))
    fill = {
        "city": _pick(rng, ("Paris", "Lyon", "Montréal", "Bruxelles", "Genève", "London", "Berlin")),
        "site": _pick(rng, ("example.com", "mon-site.fr", "apex.local")),
        "name": _pick(rng, ("Martin", "Julie", "Ahmed", "Sophie", "Thomas", "Emma")),
    }
    if lang == "fr":
        if kind == "realtime":
            user = _pick(rng, _REALTIME_FR).format(**fill)
            answer = _pick(
                rng,
                (
                    "Je n'ai pas accès à Internet ni à des informations en temps réel : je ne peux pas le savoir. "
                    "Je fonctionne uniquement en local avec ce que j'ai appris pendant mon entraînement.",
                    "Je ne peux pas le savoir : je n'ai aucun accès à des données en temps réel ni à Internet.",
                ),
            )
        elif kind == "action":
            user = _pick(rng, _ACTIONS_FR).format(**fill)
            answer = _pick(
                rng,
                (
                    "Non. Je n'ai exécuté aucune action : je peux seulement écrire du texte dans cette conversation. "
                    "Je ne prétendrai jamais avoir fait quelque chose sans preuve.",
                    "Non, je ne l'ai pas fait : je n'ai effectué aucune action en dehors de cette conversation.",
                ),
            )
        elif kind == "missing":
            user = _pick(rng, _MISSING_FR)
            answer = _pick(
                rng,
                (
                    "Il me manque une information pour répondre : peux-tu me donner le contenu concerné "
                    "(le code, le texte ou le message d'erreur) ?",
                    "Je n'ai pas assez d'informations. Peux-tu me montrer le code ou le texte en question, et "
                    "ce que tu attends ?",
                ),
            )
        else:
            user = _pick(rng, _PRIVATE_FR).format(**fill)
            answer = _pick(
                rng,
                (
                    "Je ne le sais pas : cette information ne m'a pas été donnée.",
                    "Je n'ai pas cette information, et je ne vais pas l'inventer.",
                ),
            )
    else:
        if kind == "realtime":
            user = _pick(rng, _REALTIME_EN).format(**fill)
            answer = _pick(
                rng,
                (
                    "I have no access to the Internet or to real-time information, so I cannot know that. "
                    "I run locally with only what I learned during training.",
                    "I cannot know that: I have no access to real-time data or to the Internet.",
                ),
            )
        elif kind == "action":
            user = _pick(rng, _ACTIONS_EN).format(**fill)
            answer = _pick(
                rng,
                (
                    "No. I have not performed any action: I can only write text in this conversation. "
                    "I will never claim to have done something without proof.",
                    "No, I did not: I have not performed any action outside this conversation.",
                ),
            )
        elif kind == "missing":
            user = _pick(rng, _MISSING_EN)
            answer = _pick(
                rng,
                (
                    "I am missing information to answer: can you share the content involved (the code, "
                    "the text or the error message)?",
                    "I do not have enough information. Can you show me the code or text in question, and "
                    "what you expect?",
                ),
            )
        else:
            user = _pick(rng, _PRIVATE_EN).format(**fill)
            answer = _pick(
                rng,
                (
                    "I do not know: that information has not been given to me.",
                    "I do not have that information, and I will not make it up.",
                ),
            )
    return TaskExample("honesty", lang, user, answer, {"type": "refusal", "kind": kind})


def conversation_memory(rng: random.Random, lang: str) -> TaskExample:
    name = _pick(rng, ("Camille", "Yanis", "Laura", "Nathan", "Manon", "Rayan", "Elsa", "Victor"))
    city = _pick(rng, ("Marseille", "Strasbourg", "Lille", "Grenoble", "Dijon", "Brest", "Annecy", "Tours"))
    project = _pick(
        rng,
        ("un jeu de cartes", "une API de réservation", "un site de recettes", "un outil de sauvegarde")
        if lang == "fr"
        else ("a card game", "a booking API", "a recipe website", "a backup tool"),
    )
    fact = rng.choice(("name", "city", "project"))
    if lang == "fr":
        first = f"Bonjour, je m'appelle {name}, j'habite à {city} et je travaille sur {project}."
        reply = f"Enchanté, {name} ! Comment puis-je t'aider pour {project} ?"
        question, value = {
            "name": ("Comment je m'appelle ?", name),
            "city": ("Dans quelle ville j'habite ?", city),
            "project": ("Sur quoi je travaille ?", project),
        }[fact]
        answer = {
            "name": f"Tu t'appelles {name}.",
            "city": f"Tu habites à {city}.",
            "project": f"Tu travailles sur {project}.",
        }[fact]
    else:
        first = f"Hi, my name is {name}, I live in {city} and I am working on {project}."
        reply = f"Nice to meet you, {name}! How can I help with {project}?"
        question, value = {
            "name": ("What is my name?", name),
            "city": ("Which city do I live in?", city),
            "project": ("What am I working on?", project),
        }[fact]
        answer = {
            "name": f"Your name is {name}.",
            "city": f"You live in {city}.",
            "project": f"You are working on {project}.",
        }[fact]
    return TaskExample(
        "conversation_memory",
        lang,
        question,
        answer,
        {"type": "exact", "answer": value},
        history=((first, reply),),
    )


_IDENTITY_FR = (
    (
        "Qui es-tu ?",
        "Je suis le Cerveau APEX, un modèle de langage construit et entraîné de zéro par APEX. Je fonctionne "
        "entièrement en local, sur ton ordinateur. Je suis un petit modèle : je peux me tromper, vérifie mes réponses importantes.",
    ),
    (
        "Qui t'a créé ?",
        "J'ai été construit de zéro par APEX : architecture, tokenizer et entraînement. Aucun modèle existant "
        "n'a été réutilisé.",
    ),
    ("Es-tu connecté à Internet ?", "Non. Je fonctionne en local, sans accès à Internet."),
    (
        "Que sais-tu faire ?",
        "Je peux répondre à des questions simples, calculer, écrire et expliquer de petites fonctions Python, "
        "corriger des bugs simples et suivre une conversation. Je reste un petit modèle : pour les tâches "
        "complexes, mes réponses doivent être vérifiées.",
    ),
    ("Bonjour !", "Bonjour ! Comment puis-je t'aider ?"),
    ("Salut", "Salut ! Que puis-je faire pour toi ?"),
    ("Merci beaucoup !", "Avec plaisir ! N'hésite pas si tu as d'autres questions."),
    ("Au revoir", "Au revoir et à bientôt !"),
)
_IDENTITY_EN = (
    (
        "Who are you?",
        "I am the APEX Brain, a language model built and trained from scratch by APEX. I run entirely "
        "locally, on your computer. I am a small model: I can make mistakes, so check important answers.",
    ),
    (
        "Who created you?",
        "I was built from scratch by APEX: architecture, tokenizer and training. No existing model was reused.",
    ),
    ("Are you connected to the Internet?", "No. I run locally, without Internet access."),
    (
        "What can you do?",
        "I can answer simple questions, compute, write and explain small Python functions, fix simple bugs "
        "and follow a conversation. I am still a small model: for complex tasks, my answers should be checked.",
    ),
    ("Hello!", "Hello! How can I help you?"),
    ("Hi", "Hi! What can I do for you?"),
    ("Thank you very much!", "You're welcome! Feel free to ask if you have other questions."),
    ("Goodbye", "Goodbye, see you soon!"),
)


def identity(rng: random.Random, lang: str) -> TaskExample:
    user, answer = (_IDENTITY_FR if lang == "fr" else _IDENTITY_EN)[rng.randrange(len(_IDENTITY_FR))]
    return TaskExample("identity", lang, user, answer, {"type": "none"})


_HOWTO_FR = (
    (
        "Comment créer un environnement virtuel Python ?",
        "Dans le dossier du projet :\n```\npython -m venv .venv\n```\nPuis active-le : `source .venv/bin/activate` sous Linux "
        "ou macOS, `.venv\\Scripts\\activate` sous Windows.",
    ),
    (
        "Comment installer une bibliothèque Python avec pip ?",
        "```\npython -m pip install nom-de-la-bibliotheque\n```\nDans un environnement virtuel activé, elle est installée "
        "seulement pour ce projet.",
    ),
    (
        "Comment lancer les tests avec pytest ?",
        "Depuis la racine du projet :\n```\npython -m pytest\n```\nAjoute `-q` pour "
        "une sortie courte, ou le chemin d'un fichier pour ne lancer que ses tests.",
    ),
    (
        "Comment lire un fichier texte en Python ?",
        '```python\nwith open("fichier.txt", encoding="utf-8") as f:\n    contenu = f.read()\n```\n`with` ferme le fichier '
        "automatiquement, même en cas d'erreur.",
    ),
    (
        "Comment créer une branche Git ?",
        "```\ngit switch -c nom-de-la-branche\n```\nElle part du commit courant ; "
        "`git branch` liste les branches.",
    ),
    (
        "Comment annuler les modifications non enregistrées d'un fichier avec Git ?",
        "```\ngit restore chemin/du/fichier\n```\nAttention : les modifications non commitées de ce fichier sont perdues.",
    ),
    (
        "Comment voir l'historique des commits ?",
        "```\ngit log --oneline\n```\naffiche un commit par ligne, le plus récent en premier.",
    ),
    (
        "Comment gérer une exception en Python ?",
        "```python\ntry:\n    resultat = 10 / diviseur\nexcept ZeroDivisionError:\n    resultat = None\n```\nN'attrape que les "
        "exceptions que tu sais traiter.",
    ),
    (
        "Comment trier une liste de dictionnaires par une clé en Python ?",
        '```python\npersonnes.sort(key=lambda p: p["age"])\n```\nou `sorted(personnes, key=...)` pour obtenir une nouvelle liste.',
    ),
    (
        "Comment déboguer un programme Python qui plante ?",
        "1. Lis le message d'erreur complet, en partant de la dernière ligne : elle donne le type d'exception et la cause.\n"
        "2. Repère dans la trace la ligne de ton code concernée.\n3. Reproduis le problème avec l'entrée la plus petite possible.\n"
        "4. Affiche les valeurs utiles (`print` ou `breakpoint()`) juste avant la ligne fautive.\n5. Corrige, puis ajoute un test "
        "qui aurait détecté le problème.",
    ),
    (
        "Comment écrire un test unitaire simple en Python ?",
        "```python\ndef addition(a, b):\n    return a + b\n\ndef test_addition():\n    assert addition(2, 3) == 5\n```\nLance-le "
        "avec `python -m pytest`.",
    ),
    (
        "Quelle est la différence entre une liste et un tuple en Python ?",
        "Une liste est modifiable (on peut ajouter, retirer ou changer des éléments) ; un tuple ne l'est pas. Un tuple peut "
        "servir de clé de dictionnaire, une liste non.",
    ),
)
_HOWTO_EN = (
    (
        "How do I create a Python virtual environment?",
        "In the project folder:\n```\npython -m venv .venv\n```\nThen activate it: `source .venv/bin/activate` on Linux or "
        "macOS, `.venv\\Scripts\\activate` on Windows.",
    ),
    (
        "How do I install a Python library with pip?",
        "```\npython -m pip install library-name\n```\nInside an activated virtual environment, it is installed for that project only.",
    ),
    (
        "How do I run tests with pytest?",
        "From the project root:\n```\npython -m pytest\n```\nAdd `-q` for short output, or a "
        "file path to run only its tests.",
    ),
    (
        "How do I read a text file in Python?",
        '```python\nwith open("file.txt", encoding="utf-8") as f:\n    content = f.read()\n```\n`with` closes the file '
        "automatically, even if an error occurs.",
    ),
    (
        "How do I create a Git branch?",
        "```\ngit switch -c branch-name\n```\nIt starts from the current commit; `git branch` "
        "lists the branches.",
    ),
    (
        "How do I discard uncommitted changes to a file with Git?",
        "```\ngit restore path/to/file\n```\nCareful: the uncommitted changes to that file are lost.",
    ),
    (
        "How do I see the commit history?",
        "```\ngit log --oneline\n```\nshows one commit per line, most recent first.",
    ),
    (
        "How do I handle an exception in Python?",
        "```python\ntry:\n    result = 10 / divisor\nexcept ZeroDivisionError:\n    result = None\n```\nOnly catch the "
        "exceptions you know how to handle.",
    ),
    (
        "How do I sort a list of dictionaries by a key in Python?",
        '```python\npeople.sort(key=lambda p: p["age"])\n```\nor `sorted(people, key=...)` to get a new list.',
    ),
    (
        "How do I debug a Python program that crashes?",
        "1. Read the full error message, starting from the last line: it gives the exception type and cause.\n2. Find the "
        "line of your code in the traceback.\n3. Reproduce the problem with the smallest possible input.\n4. Print the "
        "relevant values (`print` or `breakpoint()`) just before the failing line.\n5. Fix it, then add a test that would "
        "have caught the problem.",
    ),
    (
        "How do I write a simple unit test in Python?",
        "```python\ndef add(a, b):\n    return a + b\n\ndef test_add():\n    assert add(2, 3) == 5\n```\nRun it with "
        "`python -m pytest`.",
    ),
    (
        "What is the difference between a list and a tuple in Python?",
        "A list is mutable (you can add, remove or change elements); a tuple is not. A tuple can be a dictionary key, a "
        "list cannot.",
    ),
)


def howto(rng: random.Random, lang: str) -> TaskExample:
    user, answer = (_HOWTO_FR if lang == "fr" else _HOWTO_EN)[rng.randrange(len(_HOWTO_FR))]
    return TaskExample("howto", lang, user, answer, {"type": "none"})


# ---------------------------------------------------------------------------------------------- jeu complet

_FAMILIES: dict[str, tuple[Callable[..., TaskExample | None], float]] = {
    "arithmetic": (arithmetic, 3.0),
    "word_problem": (word_problem, 1.5),
    "list_ops": (list_ops, 2.0),
    "text_ops": (text_ops, 1.5),
    "conversion": (conversions, 1.0),
    "dates": (dates, 1.0),
    "logic": (logic, 1.5),
    "json": (json_format, 0.7),
    "program_output": (program_output, 2.0),
    "honesty": (honesty, 1.5),
    "conversation_memory": (conversation_memory, 1.0),
    "identity": (identity, 0.3),
    "howto": (howto, 0.6),
}
_CODE_FAMILIES = {"code_write": 2.5, "code_fix": 1.5, "code_explain": 0.8}
#: Familles notées automatiquement (les autres — identité, procédures, explications — ne servent qu'à
#: apprendre).
_SCORED_FAMILIES = frozenset(_FAMILIES) - {"identity", "howto"} | {"code_write", "code_fix"}


def with_system(example: TaskExample, rng: random.Random) -> TaskExample:
    """Consignes système vues à l'usage (60 % des exemples) : celles du Cerveau, et celles du mode « Normal »
    de l'assistant d'APEX (en français, y compris pour une question en anglais : « réponds dans la langue de
    l'utilisateur »)."""
    roll = rng.random()
    if roll >= 0.6:
        return example
    system = (SYSTEM_FR if example.lang == "fr" else SYSTEM_EN) if roll < 0.3 else _assistant_instructions()
    return TaskExample(
        example.family,
        example.lang,
        example.user,
        example.assistant,
        example.check,
        example.history,
        system,
        example.meta,
    )


def generate(
    count: int, *, seed: int, split: str = "train", exclude: frozenset[str] = frozenset()
) -> Iterator[TaskExample]:
    """`count` exemples. `split="train"` : jamais de fonction de `HELDOUT_FUNCTIONS`, jamais une consigne de
    `exclude` (les clés du jeu d'évaluation). `split="eval"` : toutes les fonctions (les réservées
    comprises)."""
    rng = random.Random(f"{split}:{seed}")
    names = list(_FAMILIES) + list(_CODE_FAMILIES)
    weights = [_FAMILIES[n][1] for n in _FAMILIES] + list(_CODE_FAMILIES.values())
    functions = [f for f in FUNCTIONS if split == "eval" or f.name not in HELDOUT_FUNCTIONS]
    produced = 0
    seen: set[str] = set()
    while produced < count:
        family = rng.choices(names, weights)[0]
        lang = "fr" if rng.random() < 0.6 else "en"
        if family in _CODE_FAMILIES:
            spec = functions[rng.randrange(len(functions))]
            example = {"code_write": write_function, "code_fix": fix_bug, "code_explain": explain_function}[
                family
            ](rng, lang, spec)
        else:
            example = _FAMILIES[family][0](rng, lang)
        if example is None:
            continue
        example = with_system(example, rng)
        key = example.key()
        if key in exclude or (split == "eval" and key in seen):
            continue
        seen.add(key)
        produced += 1
        yield example


def eval_set(per_family: int = 40, seed: int = 20261010) -> list[TaskExample]:
    """Jeu d'évaluation FIXE : `per_family` exemples notables par famille, fonctions réservées comprises."""
    buckets: dict[str, list[TaskExample]] = {}
    for example in generate(10**7, seed=seed, split="eval"):
        if example.check["type"] == "none":
            continue
        bucket = buckets.setdefault(example.family, [])
        if len(bucket) < per_family:
            bucket.append(example)
        if len(buckets) >= len(_SCORED_FAMILIES) and all(len(b) >= per_family for b in buckets.values()):
            break
    heldout = [
        write_function(random.Random(f"heldout:{f.name}:{lang}"), lang, f)
        for f in FUNCTIONS
        if f.name in HELDOUT_FUNCTIONS
        for lang in ("fr", "en")
    ]
    return [e for bucket in buckets.values() for e in bucket] + [
        TaskExample("code_write_heldout", e.lang, e.user, e.assistant, e.check, e.history, e.system, e.meta)
        for e in heldout
    ]


# ------------------------------------------------------------------------------------------------- notation

_REFUSAL_MARKERS = (
    "pas accès",
    "ne peux pas",
    "ne sais pas",
    "ne le sais pas",
    "pas cette information",
    "aucune action",
    "ne l'ai pas",
    "il me manque",
    "pas assez d'informations",
    "peux-tu",
    "inventer",
    "no access",
    "cannot",
    "do not know",
    "don't know",
    "not performed",
    "did not",
    "missing information",
    "not have enough",
    "can you",
    "make it up",
    "not been given",
)


def extract_code(answer: str) -> str:
    """Premier bloc ```python …``` (ou ``` …```) de la réponse ; à défaut, la réponse entière."""
    import re

    match = re.search(r"```(?:python|py)?\n(.*?)```", answer, re.S)
    return match.group(1) if match else answer


def run_function_tests(
    code: str, function: str, tests: list[list[Any]], timeout_s: float = 10.0
) -> tuple[bool, str]:
    """Exécute le code DANS UN PROCESSUS SÉPARÉ (interpréteur isolé `-I`, dossier temporaire, délai borné,
    aucun accès au processus d'APEX) et compare chaque appel au résultat attendu. (vrai, détail) si tous les
    tests passent. Outil d'ÉVALUATION (environnement d'entraînement) : ce n'est pas la sandbox d'APEX — pas
    de coupure réseau ni de restriction des fichiers ; ne pas l'utiliser pour du code d'origine inconnue."""
    import subprocess
    import sys
    import tempfile

    harness = (
        "import json, sys\n"
        "namespace = {}\n"
        "exec(compile(sys.stdin.read(), 'reponse.py', 'exec'), namespace)\n"
        f"f = namespace[{function!r}]\n"
        f"tests = json.loads({json.dumps(json.dumps(tests))})\n"
        "ok = 0\n"
        "for args, expected in tests:\n"
        "    try:\n"
        "        got = f(*args)\n"
        "    except Exception as exc:\n"
        "        print('ERREUR', repr(args), exc.__class__.__name__); continue\n"
        "    if isinstance(got, tuple): got = list(got)\n"
        "    if got == expected or (isinstance(got, float) and isinstance(expected, (int, float)) and abs(got - expected) < 1e-9):\n"
        "        ok += 1\n"
        "    else:\n"
        "        print('ÉCHEC', repr(args), repr(got), '!=', repr(expected))\n"
        "print('RESULTAT', ok, len(tests))\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            done = subprocess.run(  # noqa: S603 - interpréteur courant, code de la réponse notée (voir docstring)
                [sys.executable, "-I", "-c", harness],
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=tmp,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "délai dépassé"
    out = done.stdout.strip().splitlines()
    if not out or not out[-1].startswith("RESULTAT"):
        return False, (done.stderr.strip().splitlines() or ["aucune sortie"])[-1][:200]
    _, ok, total = out[-1].split()
    return ok == total, "; ".join(out[:-1])[:300] or f"{ok}/{total} tests"


def score(check: dict[str, Any], answer: str) -> bool:
    """La réponse du modèle satisfait-elle la vérification de l'exemple ?"""
    kind = check["type"]
    if kind == "exact":
        import re

        expected = str(check["answer"]).strip()
        if "\n" in expected:
            return expected in answer
        pattern = r"(?<![\w.-])" + re.escape(expected) + r"(?![\w])"
        return re.search(pattern, answer, re.IGNORECASE if expected.isalpha() else 0) is not None
    if kind == "code":
        passed, _ = run_function_tests(extract_code(answer), check["function"], check["tests"])
        return passed
    if kind == "json":
        import re

        for candidate in re.findall(r"\{.*?\}", answer, re.S):
            try:
                if json.loads(candidate) == check["answer"]:
                    return True
            except ValueError:
                continue
        return False
    if kind == "refusal":
        lowered = answer.lower()
        return any(marker in lowered for marker in _REFUSAL_MARKERS)
    if kind == "overlap":
        return overlap(check["answer"], answer) >= float(check["threshold"])
    return False


def _content_words(text: str) -> list[str]:
    import re

    return [word for word in re.findall(r"\w+", text.lower()) if len(word) > 3 or word.isdigit()]


def overlap(reference: str, answer: str) -> float:
    """Part des mots porteurs de sens de la référence présents dans la réponse. Seul le début de la réponse
    compte (trois fois la longueur de la référence) : un long déversement de texte ne « couvre » rien."""
    wanted = set(_content_words(reference))
    if not wanted:
        return 0.0
    words = _content_words(answer)
    found = set(words[: 3 * len(wanted) + 20])
    return len(wanted & found) / len(wanted)
