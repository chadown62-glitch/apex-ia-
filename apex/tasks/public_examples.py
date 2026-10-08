"""Exemples PUBLICS d'un énoncé — ce que la tâche montre déjà, rendu exécutable (08/10/2026).

Un énoncé peut écrire des exemples sous la forme d'expressions Python entre accents graves :
`` `rle_encode('aab') == 'a2b1'` ``. Ils font partie de l'énoncé, donc de ce que le modèle voit ; les exécuter sur
un candidat ne révèle RIEN de caché (les cas du Judge restent hors d'atteinte, sur l'hôte). Seules des expressions
`nom(littéraux...) == littéral` sont retenues (analyse `ast`, jamais `eval` d'un texte arbitraire) ; tout le reste
du texte est ignoré.

`examples_script` fabrique le script exécuté EN SANDBOX (jamais dans ce processus) : il importe le code candidat,
appelle chaque exemple, et publie pour chacun la valeur obtenue ou la CLASSE de l'exception — de quoi observer
« attendu X, obtenu Y » sur des entrées publiques.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

_BACKTICKS = re.compile(r"`([^`\n]{3,300})`")
_MAX_EXAMPLES = 12
_MAX_REPR = 200
_MARKER = "APEX_PUBLIC_EXAMPLES:"


@dataclass(frozen=True)
class PublicExample:
    source: str        # l'expression telle qu'écrite dans l'énoncé
    function: str
    args: tuple[Any, ...]
    expected: Any


def extract_examples(description: str, function: str | None = None) -> tuple[PublicExample, ...]:
    """Exemples `f(littéraux) == littéral` de l'énoncé (bornés, dédoublonnés, dans l'ordre)."""
    out: list[PublicExample] = []
    seen: set[str] = set()
    for match in _BACKTICKS.finditer(description or ""):
        text = match.group(1).strip()
        example = _parse(text)
        if example is None or (function is not None and example.function != function) or text in seen:
            continue
        seen.add(text)
        out.append(example)
        if len(out) >= _MAX_EXAMPLES:
            break
    return tuple(out)


def _parse(text: str) -> PublicExample | None:
    try:
        tree = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    if not (isinstance(tree, ast.Compare) and len(tree.ops) == 1 and isinstance(tree.ops[0], ast.Eq)):
        return None
    call = tree.left
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and not call.keywords):
        return None
    try:
        args = tuple(ast.literal_eval(a) for a in call.args)
        expected = ast.literal_eval(tree.comparators[0])
    except (ValueError, SyntaxError, TypeError):
        return None
    return PublicExample(source=text, function=call.func.id, args=args, expected=expected)


def examples_script(code: str, examples: tuple[PublicExample, ...], nonce: str) -> str:
    """Script autonome (à exécuter en sandbox, `python3 -I -c`) : le code candidat puis les appels publics.
    `nonce` (aléatoire, propre à l'exécution) marque la ligne de résultats : un candidat qui imprimerait une
    fausse ligne ne peut pas deviner le marqueur."""
    payload = json.dumps([{"function": e.function, "args": list(e.args), "expected": e.expected}
                          for e in examples])
    return (
        f"{code}\n\n"
        "import json as _apex_json\n"
        f"_apex_out = []\n"
        f"for _apex_ex in _apex_json.loads({payload!r}):\n"
        "    try:\n"
        "        _apex_v = globals()[_apex_ex['function']](*_apex_ex['args'])\n"
        "        _apex_out.append({'value': repr(_apex_v)[:%d], 'ok': _apex_v == _apex_ex['expected']})\n"
        "    except BaseException as _apex_e:\n"
        "        _apex_out.append({'error': type(_apex_e).__name__})\n"
        f"print({_MARKER + nonce + ':'!r} + _apex_json.dumps(_apex_out))\n"
    ) % _MAX_REPR


def read_results(stdout: str, examples: tuple[PublicExample, ...], nonce: str) -> list[dict[str, Any]] | None:
    """Résultats publiés par `examples_script` (dernière ligne marquée) ; `None` si le script n'a rien publié
    (code candidat qui ne s'importe pas, sortie forcée, délai...)."""
    marker = _MARKER + nonce + ":"
    lines = [line for line in (stdout or "").splitlines() if line.startswith(marker)]
    if not lines:
        return None
    try:
        published = json.loads(lines[-1][len(marker):])
    except ValueError:
        return None
    if not isinstance(published, list) or len(published) != len(examples):
        return None
    results = []
    for example, got in zip(examples, published, strict=True):
        expected = repr(example.expected)
        if isinstance(got, dict) and "error" in got:
            results.append({"call": example.source.split("==")[0].strip(), "expected": expected,
                            "got": None, "error": str(got["error"])[:80], "ok": False})
        else:
            value = str((got or {}).get("value"))[:_MAX_REPR]
            results.append({"call": example.source.split("==")[0].strip(), "expected": expected,
                            "got": value, "error": None, "ok": (got or {}).get("ok") is True})
    return results


def render_results(results: list[dict[str, Any]] | None) -> str:
    """Observation lisible : chaque exemple public, attendu et obtenu."""
    if results is None:
        return "exemples publics : le code n'a pas pu être exécuté jusqu'aux exemples (import ou exécution impossible)"
    if not results:
        return "exemples publics : aucun dans l'énoncé"
    lines = []
    for r in results:
        got = f"exception {r['error']}" if r["error"] else r["got"]
        lines.append(f"- {r['call']} : attendu {r['expected']}, obtenu {got} — {'OK' if r['ok'] else 'ÉCHEC'}")
    ok = sum(r["ok"] for r in results)
    return f"exemples publics de l'énoncé ({ok}/{len(results)} corrects) :\n" + "\n".join(lines)
