"""Programmation dans PLUSIEURS LANGAGES, vérifiée par de vrais compilateurs.

La même fonction composée (filtre, transformation, agrégat sur une liste d'entiers — voir
`apex.neural.compose_tasks`) est écrite en Python (Blender), C# (Unity), C++ (Unreal), JavaScript, TypeScript,
Java, Rust, Go, PHP et Ruby. Les résultats attendus viennent de la référence Python ; chaque référence d'un
autre langage est COMPILÉE et EXÉCUTÉE par le vrai compilateur pour vérifier qu'elle donne les mêmes résultats
(`verify_reference`), et la réponse du modèle est notée de la même façon (`run_tests`).

Sémantique commune à tous les langages : entiers 64 bits ; « le plus grand / le plus petit » vaut 0 quand il
n'y a aucune valeur (pas de type « absent » commun à tous) ; produits bornés (au plus 5 nombres).

Outil d'ÉVALUATION (environnement d'entraînement) : le code est compilé et exécuté dans un dossier temporaire,
avec un délai — ce n'est pas la sandbox d'APEX ; ne pas l'utiliser pour du code d'origine inconnue.
"""

# Tirages reproductibles (pas de cryptographie) ; compilateurs lancés sans shell ; gabarits de code en
# plusieurs langages, plus lisibles sur une ligne (E501).
# ruff: noqa: S311, S603, E501

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.brain.harness import program
from apex.neural.compose_tasks import (
    _EN_BEFORE,
    _NUMBER_REDUCERS,
    _fr_plural,
    _is_heldout,
    _numbers_filters,
    _numbers_maps,
    _Piece,
)
from apex.neural.tasks import TaskExample, with_system

#: Langage -> (nom affiché, outil principal, rôle pour l'utilisateur).
LANGUAGES: dict[str, tuple[str, str, str]] = {
    "python": ("Python", "python3", "Blender, outils, IA"),
    "csharp": ("C#", "dotnet", "Unity"),
    "cpp": ("C++", "g++", "Unreal Engine"),
    "javascript": ("JavaScript", "node", "web"),
    "typescript": ("TypeScript", "tsc", "web"),
    "java": ("Java", "javac", "applications"),
    "rust": ("Rust", "rustc", "systèmes"),
    "go": ("Go", "go", "serveurs"),
    "php": ("PHP", "php", "web"),
    "ruby": ("Ruby", "ruby", "scripts"),
}
#: Langages de « blocs de code » Markdown.
FENCE = {
    "python": "python",
    "csharp": "csharp",
    "cpp": "cpp",
    "javascript": "javascript",
    "typescript": "typescript",
    "java": "java",
    "rust": "rust",
    "go": "go",
    "php": "php",
    "ruby": "ruby",
}


@dataclass(frozen=True)
class Composition:
    """Une fonction composée, indépendante du langage."""

    name: str
    fr: str
    en: str
    condition: str | None  # expression Python sur x (`x % 2 == 0`)
    expression: str  # expression Python sur x (`x * x`, `abs(x)`)
    reducer: str  # somme, nombre, liste, produit, max, min
    inputs: tuple[tuple[int, ...], ...]
    heldout: bool

    @property
    def returns_list(self) -> bool:
        return self.reducer == "liste"

    def expected(self, values: tuple[int, ...]) -> Any:
        kept = [
            _py_eval(self.expression, x)
            for x in values
            if self.condition is None or _py_eval(self.condition, x)
        ]
        if self.reducer == "somme":
            return sum(kept)
        if self.reducer == "nombre":
            return len(kept)
        if self.reducer == "liste":
            return kept
        if self.reducer == "produit":
            product = 1
            for v in kept:
                product *= v
            return product
        if self.reducer == "max":
            return max(kept) if kept else 0
        return min(kept) if kept else 0


def _py_eval(expression: str, x: int) -> Any:
    return eval(expression, {"__builtins__": {}, "abs": abs}, {"x": x})  # noqa: S307 - expressions du générateur


_NOTE_ZERO = {
    "max": (" (0 s'il n'y en a aucune)", " (0 if there are none)"),
    "min": (" (0 s'il n'y en a aucune)", " (0 if there are none)"),
}


def compose(rng: random.Random) -> Composition:
    k, m = rng.randint(1, 9), rng.choice((3, 4, 5, 6, 7))
    filt = rng.choice(_numbers_filters(k, m))
    mapping: _Piece | None = rng.choice(_numbers_maps(rng.randint(2, 9)))
    reducer = rng.choice(_NUMBER_REDUCERS)
    keys = (filt.key if filt else "tous", mapping.key if mapping else "valeurs", reducer.key)
    name = f"{keys[2]}_{keys[1]}_{keys[0]}"
    note_fr, note_en = _NOTE_ZERO.get(reducer.key, (reducer.note_fr, reducer.note_en))
    masculine_note = note_fr.replace("aucune)", "aucun)")  # « nombres »
    feminine_note = masculine_note.replace("aucun)", "aucune)")  # « valeurs »
    if filt is None:
        each_fr, each_en, which_fr, which_en = "chaque nombre", "each number", "", ""
    else:
        each_fr = f"chaque nombre {filt.fr}"
        each_en = f"each {filt.en} number" if filt.key in _EN_BEFORE else f"each number {filt.en}"
        which_fr, which_en = f" qui sont {_fr_plural(filt.fr)}", f" that are {filt.en}"
    if mapping:
        fr = (
            f"renvoie {reducer.feminine} des valeurs obtenues en {mapping.fr} {each_fr} de la liste "
            f"nombres{feminine_note}"
        )
        en = f"returns {reducer.en} of the values obtained by {mapping.en} {each_en} in the list nombres{note_en}"
    else:
        fr = f"renvoie {reducer.masculine} des nombres de la liste nombres{which_fr}{masculine_note}"
        en = f"returns {reducer.en} of the numbers in the list nombres{which_en}{note_en}"
    longest = 5 if reducer.key == "produit" else 8
    inputs = ((), *(tuple(rng.randint(-15, 25) for _ in range(rng.randint(1, longest))) for _ in range(4)))
    return Composition(
        name,
        fr,
        en,
        filt.code if filt else None,
        mapping.code if mapping else "x",
        reducer.key,
        inputs,
        _is_heldout("nombres", *keys),
    )


# --------------------------------------------------------------------------------------- code de référence


def _expr(language: str, expression: str) -> str:
    """Expression Python sur x (`abs(x)`, `x * 3`, `-x`, `x % 2 == 0`) traduite dans un langage."""
    var = "$x" if language == "php" else "x"
    text = expression.replace("x", var) if language == "php" else expression
    if "abs(" in text:
        text = text.replace(
            f"abs({var})",
            {
                "python": "abs(x)",
                "csharp": "Math.Abs(x)",
                "cpp": "std::llabs(x)",
                "javascript": "Math.abs(x)",
                "typescript": "Math.abs(x)",
                "java": "Math.abs(x)",
                "rust": "x.abs()",
                "go": "abs(x)",
                "php": "abs($x)",
                "ruby": "x.abs",
            }[language],
        )
    return text


def reference(language: str, c: Composition) -> str:
    """Le code de référence de `c` dans `language` (simple, idiomatique, sans dépendance)."""
    cond = _expr(language, c.condition) if c.condition else None
    expr = _expr(language, c.expression)
    n, r = c.name, c.reducer
    if language == "python":
        keep = f" if {cond}" if cond else ""
        body = {
            "somme": "return sum(valeurs)",
            "nombre": "return len(valeurs)",
            "liste": "return valeurs",
            "produit": "produit = 1\n    for v in valeurs:\n        produit *= v\n    return produit",
            "max": "return max(valeurs) if valeurs else 0",
            "min": "return min(valeurs) if valeurs else 0",
        }[r]
        return f"def {n}(nombres):\n    valeurs = [{expr} for x in nombres{keep}]\n    {body}\n"
    if language in ("javascript", "typescript"):
        ts = language == "typescript"
        sig = (
            f"function {n}(nombres: number[]): {'number[]' if c.returns_list else 'number'}"
            if ts
            else f"function {n}(nombres)"
        )
        decl = "const valeurs: number[] = [];" if ts else "const valeurs = [];"
        push = f"if ({cond}) valeurs.push({expr});" if cond else f"valeurs.push({expr});"
        ret = {
            "somme": "valeurs.reduce((a, b) => a + b, 0)",
            "nombre": "valeurs.length",
            "liste": "valeurs",
            "produit": "valeurs.reduce((a, b) => a * b, 1)",
            "max": "valeurs.length ? Math.max(...valeurs) : 0",
            "min": "valeurs.length ? Math.min(...valeurs) : 0",
        }[r]
        return f"{sig} {{\n  {decl}\n  for (const x of nombres) {{\n    {push}\n  }}\n  return {ret};\n}}\n"
    if language == "csharp":
        rtype = "List<long>" if c.returns_list else "long"
        push = f"if ({cond}) valeurs.Add({expr});" if cond else f"valeurs.Add({expr});"
        ret = {
            "somme": "valeurs.Sum()",
            "nombre": "valeurs.Count",
            "liste": "valeurs",
            "produit": "valeurs.Aggregate(1L, (a, b) => a * b)",
            "max": "valeurs.Count > 0 ? valeurs.Max() : 0",
            "min": "valeurs.Count > 0 ? valeurs.Min() : 0",
        }[r]
        return (
            f"using System;\nusing System.Collections.Generic;\nusing System.Linq;\n\npublic static class Solution\n{{\n"
            f"    public static {rtype} {n}(long[] nombres)\n    {{\n        var valeurs = new List<long>();\n"
            f"        foreach (var x in nombres)\n        {{\n            {push}\n        }}\n        return {ret};\n    }}\n}}\n"
        )
    if language == "cpp":
        rtype = "std::vector<long long>" if c.returns_list else "long long"
        push = f"if ({cond}) valeurs.push_back({expr});" if cond else f"valeurs.push_back({expr});"
        ret = {
            "somme": "std::accumulate(valeurs.begin(), valeurs.end(), 0LL)",
            "nombre": "(long long)valeurs.size()",
            "liste": "valeurs",
            "produit": "std::accumulate(valeurs.begin(), valeurs.end(), 1LL, std::multiplies<long long>())",
            "max": "valeurs.empty() ? 0 : *std::max_element(valeurs.begin(), valeurs.end())",
            "min": "valeurs.empty() ? 0 : *std::min_element(valeurs.begin(), valeurs.end())",
        }[r]
        return (
            f"#include <algorithm>\n#include <cstdlib>\n#include <functional>\n#include <numeric>\n#include <vector>\n\n"
            f"{rtype} {n}(const std::vector<long long>& nombres) {{\n    std::vector<long long> valeurs;\n"
            f"    for (long long x : nombres) {{\n        {push}\n    }}\n    return {ret};\n}}\n"
        )
    if language == "java":
        rtype = "List<Long>" if c.returns_list else "long"
        push = f"if ({cond}) valeurs.add({expr});" if cond else f"valeurs.add({expr});"
        ret = {
            "somme": "valeurs.stream().mapToLong(Long::longValue).sum()",
            "nombre": "valeurs.size()",
            "liste": "valeurs",
            "produit": "valeurs.stream().mapToLong(Long::longValue).reduce(1L, (a, b) -> a * b)",
            "max": "valeurs.stream().mapToLong(Long::longValue).max().orElse(0L)",
            "min": "valeurs.stream().mapToLong(Long::longValue).min().orElse(0L)",
        }[r]
        return (
            f"import java.util.*;\n\nclass Solution {{\n    static {rtype} {n}(long[] nombres) {{\n"
            f"        List<Long> valeurs = new ArrayList<>();\n        for (long x : nombres) {{\n            {push}\n"
            f"        }}\n        return {ret};\n    }}\n}}\n"
        )
    if language == "rust":
        rtype = "Vec<i64>" if c.returns_list else "i64"
        push = (
            f"if {cond} {{\n            valeurs.push({expr});\n        }}"
            if cond
            else f"valeurs.push({expr});"
        )
        ret = {
            "somme": "valeurs.iter().sum()",
            "nombre": "valeurs.len() as i64",
            "liste": "valeurs",
            "produit": "valeurs.iter().product()",
            "max": "valeurs.iter().copied().max().unwrap_or(0)",
            "min": "valeurs.iter().copied().min().unwrap_or(0)",
        }[r]
        return (
            f"fn {n}(nombres: &[i64]) -> {rtype} {{\n    let mut valeurs: Vec<i64> = Vec::new();\n"
            f"    for &x in nombres {{\n        {push}\n    }}\n    {ret}\n}}\n"
        )
    if language == "go":
        rtype = "[]int64" if c.returns_list else "int64"
        push = (
            f"if {cond} {{\n\t\t\tvaleurs = append(valeurs, {expr})\n\t\t}}"
            if cond
            else f"valeurs = append(valeurs, {expr})"
        )
        tail = {
            "somme": "\tvar total int64\n\tfor _, v := range valeurs {\n\t\ttotal += v\n\t}\n\treturn total\n",
            "nombre": "\treturn int64(len(valeurs))\n",
            "liste": "\treturn valeurs\n",
            "produit": "\tvar produit int64 = 1\n\tfor _, v := range valeurs {\n\t\tproduit *= v\n\t}\n\treturn produit\n",
            "max": "\tif len(valeurs) == 0 {\n\t\treturn 0\n\t}\n\tbest := valeurs[0]\n\tfor _, v := range valeurs {\n\t\tif v > best {\n\t\t\tbest = v\n\t\t}\n\t}\n\treturn best\n",
            "min": "\tif len(valeurs) == 0 {\n\t\treturn 0\n\t}\n\tbest := valeurs[0]\n\tfor _, v := range valeurs {\n\t\tif v < best {\n\t\t\tbest = v\n\t\t}\n\t}\n\treturn best\n",
        }[r]
        helper = (
            "func abs(x int64) int64 {\n\tif x < 0 {\n\t\treturn -x\n\t}\n\treturn x\n}\n\n"
            if "abs(" in expr
            else ""
        )
        return (
            f"{helper}func {n}(nombres []int64) {rtype} {{\n\tvaleurs := []int64{{}}\n\tfor _, x := range nombres {{\n"
            f"\t\t{push}\n\t}}\n{tail}}}\n"
        )
    if language == "php":
        rtype = "array" if c.returns_list else "int"
        push = (
            f"if ({cond}) {{\n            $valeurs[] = {expr};\n        }}"
            if cond
            else f"$valeurs[] = {expr};"
        )
        ret = {
            "somme": "array_sum($valeurs)",
            "nombre": "count($valeurs)",
            "liste": "$valeurs",
            "produit": "array_product($valeurs)",
            "max": "$valeurs ? max($valeurs) : 0",
            "min": "$valeurs ? min($valeurs) : 0",
        }[r]
        return (
            f"<?php\nfunction {n}(array $nombres): {rtype}\n{{\n    $valeurs = [];\n    foreach ($nombres as $x) {{\n"
            f"        {push}\n    }}\n    return {ret};\n}}\n"
        )
    if language == "ruby":
        keep = f".select {{ |x| {cond} }}" if cond else ""
        mapped = f".map {{ |x| {expr} }}" if expr != "x" else ""
        ret = {
            "somme": "valeurs.sum",
            "nombre": "valeurs.length",
            "liste": "valeurs",
            "produit": "valeurs.reduce(1, :*)",
            "max": "valeurs.max || 0",
            "min": "valeurs.min || 0",
        }[r]
        return f"def {n}(nombres)\n  valeurs = nombres{keep}{mapped}\n  {ret}\nend\n"
    raise ValueError(f"langage inconnu : {language}")


def signature(language: str, c: Composition) -> str:
    n, lst = c.name, c.returns_list
    return {
        "python": f"{n}(nombres)",
        "csharp": f"public static {'List<long>' if lst else 'long'} {n}(long[] nombres)",
        "cpp": f"{'std::vector<long long>' if lst else 'long long'} {n}(const std::vector<long long>& nombres)",
        "javascript": f"function {n}(nombres)",
        "typescript": f"function {n}(nombres: number[]): {'number[]' if lst else 'number'}",
        "java": f"static {'List<Long>' if lst else 'long'} {n}(long[] nombres)",
        "rust": f"fn {n}(nombres: &[i64]) -> {'Vec<i64>' if lst else 'i64'}",
        "go": f"func {n}(nombres []int64) {'[]int64' if lst else 'int64'}",
        "php": f"function {n}(array $nombres): {'array' if lst else 'int'}",
        "ruby": f"def {n}(nombres)",
    }[language]


# --------------------------------------------------------------------------------------------- exécution


#: Où rustup et cargo rangent la chaîne Rust installée (le dossier personnel des tests est temporaire).
_TOOLCHAIN_HOMES = {
    "RUSTUP_HOME": os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")),
    "CARGO_HOME": os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")),
}
#: Cache de compilation de Go partagé (sans lui, chaque test recompile la bibliothèque standard).
_GO_CACHE = str(Path(tempfile.gettempdir()) / "apex-polyglot-go-cache")


def available(language: str) -> bool:
    return shutil.which(LANGUAGES[language][1]) is not None


def _commands(language: str, folder: Path, source: Path) -> list[list[str]]:
    if language == "python":
        return [["python3", "-I", str(source)]]
    if language == "javascript":
        return [["node", str(source)]]
    if language == "typescript":
        return [
            ["tsc", "--strict", "--target", "es2020", "--outDir", str(folder / "out"), str(source)],
            ["node", str(folder / "out" / "main.js")],
        ]
    if language == "csharp":
        sdk = Path(shutil.which("dotnet") or "dotnet").resolve().parent
        runtime = max((sdk / "shared" / "Microsoft.NETCore.App").glob("*"), key=lambda p: p.name)
        compiler = max((sdk / "sdk").glob("*/Roslyn/bincore/csc.dll"))
        refs = [
            f"-r:{runtime / dll}"
            for dll in (
                "System.Runtime.dll",
                "System.Console.dll",
                "System.Linq.dll",
                "System.Collections.dll",
                "System.Private.CoreLib.dll",
            )
        ]
        (folder / "main.runtimeconfig.json").write_text(
            json.dumps(
                {
                    "runtimeOptions": {
                        "tfm": "net8.0",
                        "framework": {"name": "Microsoft.NETCore.App", "version": runtime.name},
                    }
                }
            )
        )
        return [
            [
                "dotnet",
                str(compiler),
                "-nologo",
                "-nowarn:CS8019",
                f"-out:{folder / 'main.dll'}",
                *refs,
                str(source),
            ],
            ["dotnet", str(folder / "main.dll")],
        ]
    if language == "cpp":
        return [
            ["g++", "-std=c++17", "-O0", "-o", str(folder / "main"), str(source)],
            [str(folder / "main")],
        ]
    if language == "java":
        return [["javac", "-d", str(folder), str(source)], ["java", "-cp", str(folder), "Main"]]
    if language == "rust":
        return [
            ["rustc", "--edition", "2021", "-o", str(folder / "main"), str(source)],
            [str(folder / "main")],
        ]
    if language == "go":
        return [["go", "run", str(source)]]
    if language == "php":
        return [["php", str(source)]]
    if language == "ruby":
        return [["ruby", str(source)]]
    raise ValueError(language)


def commands(language: str, folder: Path, source: Path) -> list[list[str]]:
    """Commandes de compilation puis d'exécution de `source` (dans `folder`), dans l'ordre."""
    return _commands(language, folder, source)


def run_tests(
    language: str, code: str, name: str, tests: list[list[Any]], timeout_s: float = 60.0
) -> tuple[bool | None, str]:
    """Compile et exécute `code` + un programme principal qui appelle `name` sur chaque test. Rend (vrai si
    tous les résultats sont justes, détail) ; (None, raison) si le compilateur du langage est absent."""
    if not available(language):
        return None, f"{LANGUAGES[language][1]} absent : non vérifiable ici"
    inputs = [t[0][0] for t in tests]
    file_name, source_text = program(language, code, name, inputs)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = folder / file_name
        source.write_text(source_text, encoding="utf-8")
        output = ""
        for command in _commands(language, folder, source):
            try:
                done = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    cwd=tmp,
                    env={
                        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "HOME": tmp,
                        "GOCACHE": _GO_CACHE,
                        "GOPATH": f"{tmp}/gopath",
                        "DOTNET_CLI_HOME": tmp,
                        "DOTNET_NOLOGO": "1",
                        "LANG": "C.UTF-8",
                        **_TOOLCHAIN_HOMES,
                    },
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return False, "délai dépassé"
            if done.returncode != 0:
                return False, (done.stderr or done.stdout).strip()[-400:]
            output = done.stdout
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) != len(tests):
        return False, f"{len(lines)} sortie(s) pour {len(tests)} test(s)"
    for line, (_args, expected) in zip(lines, tests, strict=True):
        try:
            got = json.loads(line)
        except ValueError:
            return False, f"sortie illisible : {line[:80]}"
        if got != expected:
            return False, f"{got!r} au lieu de {expected!r}"
    return True, f"{len(tests)}/{len(tests)} tests"


def tests_of(c: Composition) -> list[list[Any]]:
    return [[[list(values)], c.expected(values)] for values in c.inputs]


def verify_reference(language: str, c: Composition) -> tuple[bool | None, str]:
    return run_tests(language, reference(language, c), c.name, tests_of(c))


# ------------------------------------------------------------------------------------------------ exercices


def example(rng: random.Random, language: str, c: Composition, *, family: str) -> TaskExample:
    label = LANGUAGES[language][0]
    lang = rng.choice(("fr", "en"))
    sig = signature(language, c)
    where = {"csharp": " de la classe statique `Solution`", "java": " de la classe `Solution`"}.get(
        language, ""
    )
    where_en = {"csharp": " in the static class `Solution`", "java": " in the class `Solution`"}.get(
        language, ""
    )
    if lang == "fr":
        user = rng.choice(
            (
                f"Écris en {label} `{sig}`{where} : elle {c.fr}.",
                f"Écris une fonction {label} `{sig}`{where} qui {c.fr}.",
            )
        )
    else:
        user = rng.choice(
            (
                f"Write in {label} `{sig}`{where_en}: it {c.en}.",
                f"Write a {label} function `{sig}`{where_en} that {c.en}.",
            )
        )
    code = reference(language, c)
    return TaskExample(
        family,
        lang,
        user,
        f"```{FENCE[language]}\n{code}```",
        {"type": "polyglot", "language": language, "function": c.name, "tests": tests_of(c)},
        meta={"function": c.name, "language": language, "heldout": c.heldout},
    )


def polyglot_examples(
    count_per_language: int,
    *,
    seed: int,
    split: str,
    languages: tuple[str, ...] = tuple(LANGUAGES),
    exclude: frozenset[str] = frozenset(),
    check: Callable[[str, Composition], bool] | None = None,
) -> list[TaskExample]:
    """`split="train"` : `count_per_language` exercices par langage, jamais une combinaison réservée.
    `split="eval"` : par langage, `count_per_language` combinaisons vues (`code_<langage>`) et autant de
    réservées (`code_<langage>_heldout`), chaque référence vérifiée par `check` (le vrai compilateur).
    `exclude` : noms de fonctions à ne jamais produire (ceux de l'évaluation)."""
    rng = random.Random(f"polyglot:{split}:{seed}")
    out: list[TaskExample] = []
    for language in languages:
        wanted = (
            {"seen": count_per_language}
            if split == "train"
            else {"seen": count_per_language, "heldout": count_per_language}
        )
        used: set[str] = set()
        misses = 0
        while any(wanted.values()) and misses < 20_000:
            c = compose(rng)
            kind = "heldout" if c.heldout else "seen"
            if (
                (split == "train" and c.heldout)
                or wanted.get(kind, 0) <= 0
                or c.name in used
                or c.name in exclude
            ):
                misses += 1
                continue
            if check is not None and not check(language, c):
                misses += 1
                continue
            misses = 0
            used.add(c.name)
            wanted[kind] -= 1
            family = f"code_{language}" + ("_heldout" if kind == "heldout" else "")
            out.append(with_system(example(rng, language, c, family=family), rng))
    return out
