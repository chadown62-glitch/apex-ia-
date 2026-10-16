"""Vérification croisée du code du Cerveau APEX par sa propre version Python.

Mesuré le 16/10/2026 (docs/CERVEAU_APEX_V2.md §9 quinquies) : les erreurs de code que la confiance laisse
passer sont des fautes de SENS (« < » au lieu de « > », un filtre ajouté) dans du code qui compile ; la
compilation seule n'en attrape que 2 sur 26. Or le Cerveau écrit juste la même fonction en Python. Méthode :

1. `parse_answer` : la réponse est-elle une fonction de la forme apprise (un seul paramètre, une liste
   d'entiers), dans un langage connu ? Sinon, rien n'est vérifié, et rien n'est deviné.
2. `twin_prefix` : début imposé de la version Python ; le Cerveau la complète en glouton, d'après la même
   conversation (`apex.models.apex_brain.ApexBrainProvider.python_twin`). `twin_code` la valide.
3. Les deux versions s'exécutent sur les MÊMES entrées générées (`inputs_for`, jamais des tests cachés) :
   `program` et `commands` donnent les fichiers et les commandes ; l'APPELANT les exécute (sandbox dans
   l'assistant, sous-processus dans l'évaluation). `parse_outputs` lit les sorties.
4. `verdict` : `agree`, `disagree`, `answer_fails` (la réponse ne compile pas ou plante) ou `unverifiable`.

Ce n'est jamais une preuve de justesse : deux versions qui concordent peuvent être fausses ensemble. Une
divergence, elle, prouve qu'au moins l'une des deux est fausse."""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apex.brain.harness import program as harness_program

#: Étiquettes de bloc de code Markdown -> langage (`apex.neural.polyglot.LANGUAGES`).
FENCES = {
    "python": "python", "py": "python", "csharp": "csharp", "cs": "csharp", "c#": "csharp", "cpp": "cpp",
    "c++": "cpp", "javascript": "javascript", "js": "javascript", "typescript": "typescript",
    "ts": "typescript",
    "java": "java", "rust": "rust", "rs": "rust", "go": "go", "golang": "go", "php": "php", "ruby": "ruby",
    "rb": "ruby",
}
#: Signature de la forme apprise (un seul paramètre : une liste d'entiers), par langage : le nom en groupe
#: 1.
_SIGNATURES = {
    "python": r"def\s+(\w+)\s*\(\s*\w+\s*(?::\s*list\[int\]\s*)?\)",
    "csharp": r"static\s+[\w<>\[\]]+\s+(\w+)\s*\(\s*long\[\]\s+\w+\s*\)",
    "cpp": r"(\w+)\s*\(\s*const\s+std::vector<\s*long\s+long\s*>\s*&\s*\w+\s*\)",
    "javascript": r"function\s+(\w+)\s*\(\s*\w+\s*\)",
    "typescript": r"function\s+(\w+)\s*\(\s*\w+\s*:\s*number\[\]\s*\)",
    "java": r"static\s+[\w<>\[\]]+\s+(\w+)\s*\(\s*long\[\]\s+\w+\s*\)",
    "rust": r"fn\s+(\w+)\s*\(\s*\w+\s*:\s*&\[i64\]\s*\)",
    "go": r"func\s+(\w+)\s*\(\s*\w+\s+\[\]int64\s*\)",
    "php": r"function\s+(\w+)\s*\(\s*(?:array\s+)?\$\w+\s*\)",
    "ruby": r"def\s+(\w+)\s*\(\s*\w+\s*\)",
}
#: Outil principal de chaque langage (doit exister dans l'environnement d'exécution).
TOOLCHAINS = {
    "python": "python3", "csharp": "dotnet", "cpp": "g++", "javascript": "node", "typescript": "tsc",
    "java": "javac", "rust": "rustc", "go": "go", "php": "php", "ruby": "ruby",
}
#: Disque d'une exécution, au-delà du réglage de la sandbox (mesuré le 16/10/2026 : avec 64 Mo, Go manque de
#: place pour son cache de compilation et le compilateur C# dépasse la taille de fichier permise ; 256
#: suffisent).
DISK_MB = {"go": 256, "csharp": 256}
INPUTS = 13
#: Sonde : une fonction connue pour être JUSTE (la somme), dans chaque langage. Si elle échoue dans
#: l'environnement d'exécution, ce langage y est « non vérifiable » : un échec n'y prouve rien sur la
#: réponse.
PROBE_FUNCTION = "apex_sonde"
PROBE_INPUTS = [[1, 2, 3], []]
PROBE_OUTPUTS = [6, 0]
PROBES = {
    "python": "def apex_sonde(nombres):\n    return sum(nombres)\n",
    "csharp": ("using System.Linq;\npublic static class Solution\n{\n"
               "    public static long apex_sonde(long[] nombres)\n    {\n"
               "        return nombres.Sum();\n    }\n}\n"),
    "cpp": ("#include <vector>\nlong long apex_sonde(const std::vector<long long>& nombres)\n"
            "{\n    long long s = 0;\n    for (long long x : nombres) s += x;\n    return s;\n}\n"),
    "javascript": "function apex_sonde(nombres) {\n  return nombres.reduce((a, b) => a + b, 0);\n}\n",
    "typescript": ("function apex_sonde(nombres: number[]): number {\n"
                   "  return nombres.reduce((a, b) => a + b, 0);\n}\n"),
    "java": ("class Solution {\n    static long apex_sonde(long[] nombres) {\n        long s = 0;\n"
             "        for (long x : nombres) s += x;\n        return s;\n    }\n}\n"),
    "rust": "fn apex_sonde(nombres: &[i64]) -> i64 {\n    nombres.iter().sum()\n}\n",
    "go": ("func apex_sonde(nombres []int64) int64 {\n\tvar s int64\n\tfor _, x := range nombres {\n"
           "\t\ts += x\n\t}\n\treturn s\n}\n"),
    "php": "<?php\nfunction apex_sonde(array $nombres): int\n{\n    return array_sum($nombres);\n}\n",
    "ruby": "def apex_sonde(nombres)\n  nombres.sum\nend\n",
}


@dataclass(frozen=True)
class CodeAnswer:
    language: str
    code: str
    function: str


@dataclass(frozen=True)
class Verdict:
    #: `agree`, `disagree`, `answer_fails` ou `unverifiable`.
    status: str
    detail: str
    #: Première entrée où les deux versions divergent, et leurs deux sorties (pour l'utilisateur).
    example: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "example": self.example}


def parse_answer(text: str) -> CodeAnswer | None:
    """Le premier bloc de code de la réponse, s'il définit UNE fonction de la forme apprise."""
    match = re.search(r"```([\w+#-]*)[ \t]*\n(.*?)```", text, re.S)
    if match is None:
        return None
    language = FENCES.get(match.group(1).strip().lower())
    if language is None:
        return None
    code = match.group(2)
    names = re.findall(_SIGNATURES[language], code)
    if len(names) != 1:
        return None
    return CodeAnswer(language=language, code=code, function=names[0])


def twin_prefix(answer: CodeAnswer) -> str:
    """Début imposé de la version Python. S'arrête après « ): » : « \\n    » (retour et indentation) est UN
    jeton du Cerveau ; un retour à la ligne seul le sortait de ce qu'il a appris (mesuré : code
    incohérent)."""
    return f"```python\ndef {answer.function}(nombres):"


def twin_code(prefix: str, continuation: str, function: str) -> str | None:
    """La version Python (début imposé + suite du Cerveau), si elle est utilisable : elle compile, définit
    `function` et y renvoie une valeur. Sinon None (mesuré : « def f(nombres): int », type recopié d'une
    signature PHP, rendait None partout et faisait croire à une divergence)."""
    text = prefix + continuation
    code = text.split("\n", 1)[1].split("```", 1)[0] if "\n" in text else ""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in tree.body:
        if (isinstance(node, ast.FunctionDef) and node.name == function
                and any(isinstance(n, ast.Return) and n.value is not None for n in ast.walk(node))):
            return code
    return None


def inputs_for(function: str) -> list[list[int]]:
    """Entrées générées, reproductibles (graine : le nom de la fonction) : la liste vide, puis des listes
    d'entiers de -20 à 20. Jamais les tests cachés d'une évaluation."""
    # Reproductibilité, pas de sécurité : un générateur pseudo-aléatoire ordinaire suffit.
    rng = random.Random(int(hashlib.sha256(function.encode()).hexdigest()[:12], 16))  # noqa: S311
    return [[]] + [[rng.randint(-20, 20) for _ in range(rng.randint(1, 8))] for _ in range(INPUTS - 1)]


def program(language: str, code: str, function: str, inputs: list[list[int]]) -> tuple[str, str]:
    """(nom de fichier, source) : `code` + programme principal (même harnais que l'évaluation)."""
    return harness_program(language, code, function, inputs)


def commands(language: str) -> list[list[str]]:
    """Commandes à lancer dans le dossier de travail (chemins RELATIFS), le premier élément étant le nom de
    l'outil : l'appelant le résout (`shutil.which`) et décide s'il peut l'exécuter. C# : voir aussi
    `extra_files`."""
    if language == "python":
        return [["python3", "-I", "main.py"]]
    if language == "javascript":
        return [["node", "main.js"]]
    if language == "typescript":
        return [["tsc", "--strict", "--target", "es2020", "--outDir", "out", "main.ts"],
                ["node", "out/main.js"]]
    if language == "cpp":
        return [["g++", "-std=c++17", "-O0", "-o", "main", "main.cpp"], ["./main"]]
    if language == "java":
        return [["javac", "-d", ".", "Main.java"], ["java", "-cp", ".", "Main"]]
    if language == "rust":
        return [["rustc", "--edition", "2021", "-o", "main", "main.rs"], ["./main"]]
    if language == "go":
        return [["go", "run", "main.go"]]
    if language == "php":
        return [["php", "main.php"]]
    if language == "ruby":
        return [["ruby", "main.rb"]]
    if language == "csharp":
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            return [["dotnet"]]  # absent : l'appelant le constatera
        sdk = Path(dotnet).resolve().parent
        runtime = max((sdk / "shared" / "Microsoft.NETCore.App").glob("*"), key=lambda p: p.name)
        compiler = max((sdk / "sdk").glob("*/Roslyn/bincore/csc.dll"))
        dlls = ("System.Runtime.dll", "System.Console.dll", "System.Linq.dll", "System.Collections.dll",
                "System.Private.CoreLib.dll")
        refs = [f"-r:{runtime / dll}" for dll in dlls]
        return [["dotnet", str(compiler), "-nologo", "-nowarn:CS8019", "-out:main.dll", *refs, "main.cs"],
                ["dotnet", "main.dll"]]
    raise ValueError(language)


def extra_files(language: str) -> list[tuple[str, str]]:
    """Fichiers à déposer en plus du source (C# : la configuration d'exécution .NET)."""
    if language != "csharp" or shutil.which("dotnet") is None:
        return []
    sdk = Path(shutil.which("dotnet") or "").resolve().parent
    runtime = max((sdk / "shared" / "Microsoft.NETCore.App").glob("*"), key=lambda p: p.name)
    config = {"runtimeOptions": {"tfm": "net8.0",
                                 "framework": {"name": "Microsoft.NETCore.App", "version": runtime.name}}}
    return [("main.runtimeconfig.json", json.dumps(config))]


def parse_outputs(stdout: str, expected: int) -> list[Any] | None:
    """Une valeur JSON par ligne, autant que d'entrées ; sinon None."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != expected:
        return None
    try:
        return [json.loads(line) for line in lines]
    except ValueError:
        return None


def verdict(inputs: list[list[int]], answer: list[Any] | None, twin: list[Any] | None,
            *, answer_error: str = "", twin_error: str = "") -> Verdict:
    if twin is None or any(value is None for value in twin):
        reason = twin_error or "sortie illisible"
        return Verdict("unverifiable", f"version Python inutilisable : {reason}"[:300])
    if answer is None:
        return Verdict("answer_fails", f"la réponse ne compile pas ou plante : {answer_error}"[:300])
    for values, got, expected in zip(inputs, answer, twin, strict=True):
        if got != expected:
            return Verdict("disagree", "la réponse et la version Python divergent",
                           {"input": values, "answer": got, "python": expected})
    return Verdict("agree", f"mêmes résultats sur {len(inputs)} entrées générées")
