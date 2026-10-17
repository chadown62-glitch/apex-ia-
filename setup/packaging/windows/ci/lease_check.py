"""Partage du GPU sur un vrai Windows, par le Python et le code d'APEX INSTALLÉS : le bail du Cerveau et le
vrai bail des étapes de Game Studio (`apex.game_graphics.resources.resource_lease`) s'excluent, le prêt
fait au clic de l'utilisateur (`request_gpu`) laisse la mission le prendre, et le GPU revient au Cerveau.

Le runner n'a pas de GPU : le Cerveau est placé sur un « CUDA » simulé (le bail et les verrous Windows,
eux, sont réels). Aucune mesure CUDA n'est revendiquée. Profil APEX jetable : rien n'est écrit chez APEX.

Sortie : lignes `CHECK`/`NOTE`/`VERDICT` ; code 0 si tout est vérifié, 1 sinon, 3 sans Game Studio."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="apex-lease-"))
    os.environ["APEX_HOME"] = str(home)
    from apex.models.gpu_handoff import GpuGovernor, Resident

    try:
        from apex.game_graphics.resources import resource_lease
    except ImportError as exc:
        print(f"NOTE Game Studio absent : {exc}")
        print("VERDICT BLOCKED")
        return 3
    unloads: list[str] = []
    loaded = [True]

    def unload() -> None:
        loaded[0] = False
        unloads.append("apex_brain:simulé")

    lock = threading.Lock()
    governor = GpuGovernor(cuda_available=lambda: True, cuda_memory=lambda: None)
    governor.register("apex_brain",
                      lambda: [Resident("apex_brain:simulé", lock, unload)] if loaded[0] else [])
    checks: list[tuple[str, bool, str]] = []

    def lease() -> str:
        try:
            with resource_lease(home / "mission" / "resource.lock"):
                return "obtenu"
        except OSError as exc:  # le verrou partagé, tenu ailleurs
            return f"verrou refusé ({type(exc).__name__}: {exc})"
        except RuntimeError as exc:  # les contrôles de Game Studio (autre APEX, Blender, serveur de modèle)
            return f"contrôle refusé ({exc})"

    device, reason = governor.place("auto")
    checks.append(("brain_holds_the_lease", device == "cuda" and governor.status()["lease_held"], reason))
    refused = lease()
    checks.append(("mission_lease_refused_while_the_brain_holds_it", refused.startswith("verrou refusé"),
                   refused))
    ack = governor.request_gpu(5, requester="game_studio:ci")
    checks.append(("brain_releases_on_request", bool(ack["released"]) and unloads == ["apex_brain:simulé"],
                   f"{ack['reason']} ; déchargés : {ack['unloaded']}"))
    taken = lease()
    checks.append(("mission_lease_obtained_after_the_loan", taken == "obtenu", taken))
    device, reason = governor.place("auto")
    checks.append(("brain_answers_on_cpu_during_the_loan", device == "cpu", reason))
    back = governor.return_gpu(requester="game_studio:ci")
    loaded[0] = True
    device, reason = governor.place("auto")
    checks.append(("brain_takes_the_gpu_back", bool(back["resumed"]) and device == "cuda", reason))
    for name, ok, detail in checks:
        print(f"CHECK {name} {'PASS' if ok else 'FAIL'} : {detail}")
    passed = all(ok for _, ok, _ in checks)
    print("NOTE CUDA simulé (runner sans GPU) ; bail et verrous Windows réels")
    print(f"VERDICT {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
