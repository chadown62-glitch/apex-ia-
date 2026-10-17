"""Vérification croisée du code du Cerveau APEX par sa propre version Python (`apex.brain.crosscheck`,
`apex.assistant.verification`). Exécution RÉELLE par les vrais compilateurs quand ils sont là (ignorée
sinon) ; le Cerveau lui-même n'intervient pas ici (sa mesure : docs/CERVEAU_APEX_V2.md §9 quinquies)."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from apex.assistant import verification
from apex.brain import crosscheck
from apex.neural import polyglot

LANGUAGES = [lang for lang in polyglot.LANGUAGES if lang != "python"]


def _composition(seed: int) -> polyglot.Composition:
    return polyglot.compose(random.Random(seed))


def _fenced(language: str, code: str) -> str:
    return f"Voici la fonction :\n```{polyglot.FENCE[language]}\n{code}```\n"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_learned_function_shape_is_recognized_in_every_language(language) -> None:
    c = _composition(3)
    answer = crosscheck.parse_answer(_fenced(language, polyglot.reference(language, c)))
    assert answer is not None and answer.language == language and answer.function == c.name


def test_anything_else_is_never_guessed() -> None:
    assert crosscheck.parse_answer("Pas de code ici.") is None
    assert crosscheck.parse_answer("```go\nfunc f(a []int64, b int64) int64 { return b }\n```") is None
    assert crosscheck.parse_answer("```cobol\nDISPLAY 'X'.\n```") is None
    two = "```ruby\ndef f(nombres)\n  1\nend\ndef g(nombres)\n  2\nend\n```"
    assert crosscheck.parse_answer(two) is None  # deux fonctions : laquelle vérifier ? aucune


def test_only_a_usable_python_twin_is_kept() -> None:
    prefix = "```python\ndef f(nombres):"
    twin = crosscheck.twin_code(prefix, "\n    return sum(nombres)\n```", "f")
    assert twin == "def f(nombres):\n    return sum(nombres)\n"
    assert crosscheck.twin_code(prefix, " int\n```", "f") is None  # mesuré : type PHP recopié, rendait None
    assert crosscheck.twin_code(prefix, "\n    valeurs = [x for\n```", "f") is None
    assert crosscheck.twin_code("```python\ndef g(nombres):", "\n    return 1\n```", "f") is None


def test_inputs_are_generated_and_reproducible_never_hidden_tests() -> None:
    inputs = crosscheck.inputs_for("somme_pairs_tous")
    assert inputs == crosscheck.inputs_for("somme_pairs_tous") and len(inputs) == crosscheck.INPUTS
    assert inputs[0] == [] and all(-20 <= v <= 20 for values in inputs for v in values)


def test_verdicts() -> None:
    inputs = [[], [1, 2]]
    assert crosscheck.verdict(inputs, [0, 3], [0, 3]).status == "agree"
    mismatch = crosscheck.verdict(inputs, [0, 2], [0, 3])
    assert mismatch.status == "disagree" and mismatch.example == {"input": [1, 2], "answer": 2, "python": 3}
    assert crosscheck.verdict(inputs, None, [0, 3], answer_error="syntax error").status == "answer_fails"
    assert crosscheck.verdict(inputs, [0, 3], [None, None]).status == "unverifiable"  # jumeau inutilisable
    assert crosscheck.verdict(inputs, [0, 3], None).status == "unverifiable"


def _subprocess_executor(language, files, commands):
    """Comme l'évaluation : un dossier temporaire et les vrais outils (jamais la sandbox ici)."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in files:
            Path(tmp, name).write_bytes(content)
        out = ""
        for command in commands:
            tool = command[0] if command[0].startswith("./") else shutil.which(command[0])
            if tool is None:
                return None, f"{command[0]} absent", True
            done = subprocess.run([tool, *command[1:]], capture_output=True, text=True, timeout=120,
                                  cwd=tmp, check=False, env={"PATH": os.environ["PATH"], "HOME": tmp,
                                                    "GOCACHE": polyglot._GO_CACHE, "DOTNET_CLI_HOME": tmp,
                                                    "DOTNET_NOLOGO": "1", "LANG": "C.UTF-8",
                                                    **polyglot._TOOLCHAIN_HOMES})
            if done.returncode != 0:
                return None, done.stderr[-300:], False
            out = done.stdout
    return out, "", False


def _pair() -> tuple[polyglot.Composition, polyglot.Composition]:
    """Deux fonctions de la forme apprise aux résultats différents sur les entrées générées."""
    c = _composition(5)
    for seed in range(6, 60):
        other = _composition(seed)
        python = polyglot.reference("python", other).replace(other.name, c.name)
        a = verification._run(_subprocess_executor, "python", polyglot.reference("python", c), c.name,
                              crosscheck.inputs_for(c.name))[0]
        b = verification._run(_subprocess_executor, "python", python, c.name,
                              crosscheck.inputs_for(c.name))[0]
        if a != b:
            return c, other
    raise AssertionError("aucune paire")


@pytest.mark.parametrize("language", LANGUAGES)
def test_real_compilers_agree_on_a_right_answer_and_catch_a_wrong_one(language) -> None:
    if not polyglot.available(language):
        pytest.skip(f"{polyglot.LANGUAGES[language][1]} absent")
    c, other = _pair()
    twin = polyglot.reference("python", c)
    right = _fenced(language, polyglot.reference(language, c))
    wrong = _fenced(language, polyglot.reference(language, other).replace(other.name, c.name))
    prefix_twin = twin.split(":", 1)[1]  # ce qui suit « def nom(nombres): »
    agree = verification.check(right, lambda prefix: prefix_twin + "```", _subprocess_executor)
    assert agree is not None and agree.status == "agree", agree
    caught = verification.check(wrong, lambda prefix: prefix_twin + "```", _subprocess_executor)
    assert caught is not None and caught.status == "disagree" and caught.example is not None, caught


@pytest.mark.parametrize("language", list(polyglot.LANGUAGES))
def test_every_probe_is_a_right_function_with_the_real_compilers(language) -> None:
    if not polyglot.available(language):
        pytest.skip(f"{polyglot.LANGUAGES[language][1]} absent")
    probes: verification.Probes = {}
    assert verification._ready(_subprocess_executor, language, probes) == (True, "")
    assert probes[language][0] is True  # mémorisée : pas refaite au prochain tour


def test_a_language_whose_probe_fails_here_is_unverifiable_never_wrong() -> None:
    """Exemple réel : C# dans la sandbox Linux avant ses réglages .NET (« GC heap initialization failed ») :
    une fonction JUSTE y échoue, donc un échec n'y prouve rien sur la réponse du Cerveau."""
    def broken_for_ruby(language, files, commands):
        if language == "ruby":
            return None, "GC heap initialization failed", False
        return _subprocess_executor(language, files, commands)

    c, _ = _pair()
    twin = polyglot.reference("python", c).split(":", 1)[1] + "```"
    verdict = verification.check(_fenced("ruby", polyglot.reference("ruby", c)), lambda prefix: twin,
                                 broken_for_ruby)
    assert verdict is not None and verdict.status == "unverifiable"
    assert "ruby non vérifiable" in verdict.detail


def test_a_language_the_sandbox_cannot_run_is_unverifiable_never_wrong() -> None:
    from apex.errors import IsolationUnavailable

    def windows_like_sandbox(command, files, **_):
        if command[0] != "python3":
            raise IsolationUnavailable("commande en sandbox Windows", required="python",
                                       available="appcontainer")
        return _subprocess_result(command, files)

    c, _ = _pair()
    execute = verification.sandbox_executor(windows_like_sandbox)
    twin = polyglot.reference("python", c).split(":", 1)[1] + "```"
    answer = _fenced("ruby", polyglot.reference("ruby", c))
    verdict = verification.check(answer, lambda prefix: twin, execute)
    assert verdict is not None and verdict.status == "unverifiable"
    assert "n'exécute pas ruby" in verdict.detail


def _subprocess_result(command, files):
    """Résultat au format de la sandbox, exécuté ici (Python seulement)."""
    from apex.obs.status import Measurement

    with tempfile.TemporaryDirectory() as tmp:
        for name, content in files:
            Path(tmp, name).write_bytes(content)
        done = subprocess.run([shutil.which("python3") or "python3", *command[1:]], capture_output=True,
                              text=True, cwd=tmp, timeout=60, check=False)
    status = type("S", (), {"value": "success" if done.returncode == 0 else "failed"})()
    return type("R", (), {"status": status, "exit_code": Measurement.real(done.returncode),
                          "stdout": done.stdout, "stderr": done.stderr})()
