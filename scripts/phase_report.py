#!/usr/bin/env python3
"""Génère la section « état des abstractions » du rapport de phase.

Le rapport n'est pas rédigé à la main : il est produit depuis le registre, qui
est lui-même vérifié par les tests. Un rapport rédigé à la main dériverait de
la réalité en quelques semaines — et c'est précisément ce que le principe 15
cherche à empêcher.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apex.interfaces.registry import ABSTRACTIONS, ImplStatus, module_importable  # noqa: E402

LABEL = {
    ImplStatus.IMPLEMENTED: "implémenté",
    ImplStatus.STUB: "stub",
    ImplStatus.DEFINED: "défini, non implémenté",
    ImplStatus.PLANNED: "planifié",
}


def main() -> int:
    counts = {status: 0 for status in ImplStatus}
    for item in ABSTRACTIONS:
        counts[item.status] += 1

    print(f"Total : {len(ABSTRACTIONS)} abstractions")
    for status, count in counts.items():
        print(f"  {LABEL[status]:<24} {count}")
    print()
    print(f"| {'Abstraction':<20} | {'État':<22} | Déf. | Impl. | Module vérifié |")
    print(f"|{'-' * 22}|{'-' * 24}|------|-------|----------------|")
    for item in sorted(ABSTRACTIONS, key=lambda a: (a.phase_implemented, a.name)):
        verified = "oui" if module_importable(item) else "—"
        print(
            f"| {item.name:<20} | {LABEL[item.status]:<22} | "
            f"{item.phase_defined:>4} | {item.phase_implemented:>5} | {verified:<14} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
