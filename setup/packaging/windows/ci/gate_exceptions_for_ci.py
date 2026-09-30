"""CI UNIQUEMENT (runner GitHub Windows Server + WSL2 jetable) : écrit le gate.exceptions.yaml de la
distribution de test, comme le propriétaire d'une machine le fait pour la sienne.

`launch.sh serve` n'exécute `apex serve` que si `apex gate` est concluant. Sur le runner, sept contrôles
restent non concluants (constaté au run 11) : GPU absent (`not_applicable`) et sondes que launch.sh
n'autorise jamais (`not_authorized`). Sans dérogation, le backend ne démarre jamais et aucun scénario
backend du Panel ne peut être validé.

Règles (conservatrices) :
  - seuls les contrôles UNKNOWN dont la raison est `not_applicable` ou `not_authorized` sont excusés ;
  - jamais un FAIL, jamais un DEGRADED, jamais un UNKNOWN d'une autre nature ;
  - expiration au lendemain : la dérogation ne survit pas au runner ;
  - la liste excusée est affichée dans le journal de la CI.

Usage : gate_exceptions_for_ci.py <rapport JSON de `apex gate --json`> <gate.exceptions.yaml à écrire>
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

EXCUSABLE_REASONS = ("not_applicable", "not_authorized")
APPROVED_BY = "ci-windows-desktop (runner GitHub jetable)"


def excusable_checks(report: dict) -> list[dict]:
    return [
        c for c in report.get("checks", [])
        if c.get("status") == "UNKNOWN" and c.get("unknown_reason") in EXCUSABLE_REASONS
    ]


def render(checks: list[dict], today: _dt.date) -> str:
    expires = (today + _dt.timedelta(days=1)).isoformat()
    lines = [
        "# Écrit par setup/packaging/windows/ci/gate_exceptions_for_ci.py — runner CI uniquement.",
        "exceptions:",
    ]
    for c in checks:
        reason = (f"runner CI jetable : {c['unknown_reason']} — {c.get('message', '')}. "
                  "Validation du Panel Windows uniquement ; aucune garantie de sécurité abaissée.")
        lines += [
            f"  - check_id: {c['check_id']}",
            f"    reason: {json.dumps(reason, ensure_ascii=False)}",
            f"    approved_by: {json.dumps(APPROVED_BY, ensure_ascii=False)}",
            f"    expires_on: {expires}",
        ]
    if not checks:
        lines[-1] = "exceptions: []"
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    checks = excusable_checks(report)
    Path(argv[2]).write_text(render(checks, _dt.date.today()), encoding="utf-8")
    kept = [f"{c['check_id']}={c.get('status')}" for c in report.get("checks", [])
            if c.get("status") not in ("PASS",) and c not in checks]
    print("excusés (CI) : " + (", ".join(f"{c['check_id']} ({c['unknown_reason']})" for c in checks) or "aucun"))
    print("non excusés : " + (", ".join(kept) or "aucun"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
