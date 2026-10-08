"""Souvenirs d'exécution ACTIONNABLES et mesure de leur UTILISATION (08/10/2026, docs/VALIDATION_REELLE.md §6.3).

Constat (§6, H-M1) : un souvenir d'exécution était injecté comme une fenêtre de 600 caractères centrée sur le
premier mot de la requête — en pratique surtout l'énoncé d'une AUTRE tâche, le code coupé, aucune leçon. Ici :

- `execution_view` rend un souvenir `task_run:*` dans un ordre utile — ce qui a été fait, avec quel résultat, la
  leçon tirée (diagnostic et stratégie qui a marché, si la TaskRun en a produit), le code (borné), puis le début
  de l'énoncé — à partir du texte écrit par `apex.memory.indexing.index_task_run` (aucune donnée nouvelle) ;
- `use_evidence` dit si un souvenir MONTRÉ a été UTILISÉ (H-M2) : les littéraux et noms distinctifs du souvenir
  (absents de l'énoncé courant) retrouvés dans le code produit. C'est une mesure objective et vérifiable, pas
  un jugement du modèle ; une coïncidence reste possible et l'analyse la contrôle par un bras placebo.
"""

from __future__ import annotations

import re

_MAX_CODE_CHARS = 1_500
_MAX_STATEMENT_CHARS = 500
_SECTION = re.compile(r"^(Tâche|Énoncé|Résultat|Leçon) : ", re.M)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?![\w.])")
_STRING = re.compile(r"""(['"])([^'"\n]{2,40})\1""")
_DEF = re.compile(r"\bdef\s+([A-Za-z_]\w*)")
_CONST = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", re.M)


def _sections(content: str) -> tuple[dict[str, str], str]:
    """En-tête (`Tâche`, `Énoncé` — éventuellement sur plusieurs lignes —, `Résultat`, `Leçon`) et le reste (faits
    d'échec, code), tels qu'écrits par `index_task_run`."""
    fields: dict[str, str] = {}
    headers = list(_SECTION.finditer(content))
    if not headers or headers[0].group(1) != "Tâche":
        return fields, content
    end = len(content)
    for i, match in enumerate(headers):
        stop = headers[i + 1].start() if i + 1 < len(headers) else None
        if stop is None:  # dernier en-tête : sa valeur s'arrête à la fin de SA ligne, le reste est le corps
            line_end = content.find("\n", match.end())
            stop = end if line_end < 0 else line_end
        fields[match.group(1)] = content[match.end():stop].strip()
    return fields, content[stop:].strip()


def execution_view(content: str, *, max_code_chars: int = _MAX_CODE_CHARS,
                   max_statement_chars: int = _MAX_STATEMENT_CHARS) -> str:
    fields, rest = _sections(content)
    if "Tâche" not in fields:  # pas un souvenir d'exécution structuré : rendu inchangé, borné
        return content[: max_code_chars + max_statement_chars]
    parts = [f"Tâche : {fields['Tâche']}"]
    if "Résultat" in fields:
        parts.append(f"Résultat : {fields['Résultat']}")
    if "Leçon" in fields:
        parts.append(f"Leçon : {fields['Leçon']}")
    if rest:
        code = rest if len(rest) <= max_code_chars else rest[:max_code_chars] + "\n# … [code tronqué]"
        parts.append(code)
    if "Énoncé" in fields:
        statement = fields["Énoncé"]
        if len(statement) > max_statement_chars:
            statement = statement[:max_statement_chars] + "…"
        parts.append(f"Énoncé de cette tâche passée : {statement}")
    return "\n".join(parts)


def _numbers(text: str) -> set[float]:
    out = set()
    for raw in _NUMBER.findall(text):
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            continue
        # 0, 1, 2… et les petits entiers sont partout : jamais une preuve d'utilisation.
        if "." in raw or "," in raw or abs(value) >= 10:
            out.add(round(value, 6))
    return out


def _distinctive(text: str) -> tuple[set[float], set[str], set[str]]:
    strings = {m.group(2) for m in _STRING.finditer(text)}
    names = set(_DEF.findall(text)) | set(_CONST.findall(text))
    return _numbers(text), strings, names


def use_evidence(shown: str, statement: str, code: str) -> list[str]:
    """Éléments distinctifs du souvenir MONTRÉ, absents de l'énoncé courant, présents dans le code produit."""
    if not shown or not code:
        return []
    numbers, strings, names = _distinctive(shown)
    s_numbers, s_strings, s_names = _distinctive(statement)
    c_numbers, c_strings, _ = _distinctive(code)
    evidence = [f"nombre {n:g}" for n in sorted(numbers - s_numbers) if n in c_numbers]
    evidence += [f"chaîne {s!r}" for s in sorted(strings - s_strings)
                 if s in c_strings and s not in statement]
    evidence += [f"nom {n}" for n in sorted(names - s_names)
                 if n not in statement and re.search(rf"\b{re.escape(n)}\b", code)]
    return evidence[:12]
