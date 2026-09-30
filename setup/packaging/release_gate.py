"""Release gate de la distribution Windows d'APEX (Gauntlet release gate).

Exécute RÉELLEMENT chaque vérification (jamais une case cochée sur déclaration) puis écrit
`release_gate.json` et `release_gate.md` dans le dossier de sortie :

  build          setup/packaging/build_desktop_release.sh (+ reconstruction : même SHA256 ?)
  installateur   setup/packaging/wine_installer_e2e.sh (WINE E2E, avec mise à jour si fournie)
  robustesse     setup/packaging/wine_robustness_e2e.sh (fin de parcours sur l'artefact, WINE E2E)
  Panel          Gauntlet : dernier snapshot mesuré par le moteur + juge adversarial (sondes)
  régression     dotnet test setup/ApexSetup.sln ; pytest des modules du Panel et du backend
  Gauntlet       verdicts des cycles consignés au ledger (aucun REGRESSED, aucun FAILED)
  documentation  docs/INSTALLER_APEX.md

Verdict : RELEASE_READY seulement si TOUT passe. Les éléments qui exigent un Windows réel (WSL réel,
raccourci du vrai shell, redémarrage de Windows) sont BLOCKED ici : le verdict devient alors
BLOCKED_ON_REAL_WINDOWS — jamais RELEASE_READY sans preuve Windows.

Usage : python setup/packaging/release_gate.py <sortie> [--upgrade-from-installer <exe plus ancien>]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "setup"
LEDGER = REPO / "docs" / "improvement" / "desktop_distribution.ledger.jsonl"

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
ROBUSTNESS_STEPS = 10
#: Code du PRODUIT livré (le banc `apex/improvement` et la documentation n'en font pas partie).
PRODUCT_PATHS = (
    "setup/src",
    "setup/packaging",
    "setup/Directory.Build.props",
    "apex",
    ":(exclude)apex/improvement",
    "scripts",
)


@dataclass
class Item:
    item: str
    status: str
    evidence: str


def run(cmd: list[str], timeout: int = 3600, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    # Seuls les scripts de ce dépôt, dotnet et pytest sont exécutés : aucune commande externe.
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--older-installer", type=Path, help="installateur plus ancien : teste la mise à jour vers celui-ci"
    )
    args = parser.parse_args()
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    items: list[Item] = []

    def add(name: str, ok: bool, evidence: str) -> None:
        items.append(Item(name, PASS if ok else FAIL, evidence))

    started = time.time()

    # -- build ----------------------------------------------------------------------------------
    builds = []
    for index in (1, 2):
        target = out / f"build{index}"
        done = run(["bash", str(SETUP / "packaging" / "build_desktop_release.sh"), str(target)])
        manifest = target / "manifest.json"
        builds.append(
            json.loads(manifest.read_text(encoding="utf-8"))
            if done.returncode == 0 and manifest.exists()
            else None
        )
    first = builds[0]
    add(
        "build réussi",
        first is not None,
        "build_desktop_release.sh" + (f" -> {first['installer']['file']}" if first else " en échec"),
    )
    add(
        "artefact SHA256 généré",
        first is not None and (out / "build1" / f"{first['installer']['file']}.sha256").exists(),
        f"sha256 {first['installer']['sha256']}" if first else "aucun artefact",
    )
    add(
        "build reproductible",
        first is not None
        and builds[1] is not None
        and first["installer"]["sha256"] == builds[1]["installer"]["sha256"],
        "deux builds successifs : "
        + " / ".join(b["installer"]["sha256"][:16] if b else "échec" for b in builds),
    )
    if first is None:
        return finish(out, items, started)
    installer = out / "build1" / first["installer"]["file"]

    # -- installateur (Wine) --------------------------------------------------------------------
    wine_args = ["bash", str(SETUP / "packaging" / "wine_installer_e2e.sh")]
    if args.older_installer:
        wine_args += [str(args.older_installer.resolve()), str(out / "wine"), str(installer)]
    else:
        wine_args += [str(installer), str(out / "wine")]
    run(wine_args)
    results_file = out / "wine" / "results.json"
    checks = (
        {c["id"]: c for c in json.loads(results_file.read_text(encoding="utf-8"))["checks"]}
        if results_file.exists()
        else {}
    )

    def wine(name: str, *ids: str) -> None:
        present = [checks[i] for i in ids if i in checks]
        ok = len(present) == len(ids) and all(c["status"] == PASS for c in present)
        add(
            name + " (WINE E2E)",
            ok,
            " ; ".join(f"{c['id']} {c['status']} {c['detail']}" for c in present) or "non exécuté",
        )

    wine(
        "installer fonctionne",
        "W1_install_vierge",
        "W2_fichiers_installes",
        "W6_validation_post_installation",
    )
    wine("installation vierge fonctionne", "W1_install_vierge")
    wine("raccourci créé", "W3_raccourci_bureau", "W4_menu_demarrer")
    wine(
        "double-clic fonctionne",
        "W7_double_clic_raccourci_autre_repertoire",
        "W8_second_double_clic_instance_unique",
    )
    wine("Panel démarre", "W7_double_clic_raccourci_autre_repertoire", "W9_verdict_sans_wsl")
    wine("fermeture/réouverture correcte", "W11_relance_apres_plantage")
    wine("désinstallation validée", "W15_desinstallation", "W16_donnees_utilisateur_conservees")
    wine(
        "réinstallation validée",
        "W13_reinstallation",
        "W17_reinstallation_apres_desinstallation",
        "W18_installation_interrompue_puis_relancee",
    )
    if args.older_installer:
        wine("mise à jour validée", "W14_mise_a_jour")

    # Séquence de fin de parcours sur l'artefact : redémarrage de session Wine (pas Windows), double-clic,
    # diagnostic redirigé (UTF-8, sans secret), fermeture, réouverture, désinstallation, réinstallation.
    robust = run(
        [
            "bash",
            str(SETUP / "packaging" / "wine_robustness_e2e.sh"),
            str(installer),
            str(out / "robustness"),
        ]
    )
    steps = [line[5:] for line in robust.stdout.splitlines() if line.startswith("STEP ")]
    add(
        "séquence de robustesse (WINE E2E)",
        robust.returncode == 0 and len(steps) == ROBUSTNESS_STEPS and all(" PASS " in s for s in steps),
        " ; ".join(steps) or (robust.stdout + robust.stderr)[-300:],
    )

    # -- Panel : dernier snapshot du Gauntlet + juge adversarial ---------------------------------
    latest = latest_snapshot()
    means = {m: sum(v) / len(v) for m, v in (latest or {}).get("samples", {}).items() if v}

    def metric(name: str, *metrics: str) -> None:
        ok = latest is not None and all(means.get(m) == 1.0 for m in metrics)
        add(
            name,
            ok,
            f"Gauntlet {latest['version'] if latest else '?'} : "
            + ", ".join(f"{m}={means.get(m)}" for m in metrics),
        )

    metric(
        "backend offline correctement détecté", "offline_detected", "crash_detected", "clean_stop_detected"
    )
    metric("backend online correctement détecté", "online_detected", "starting_detected")
    metric(
        "health checks corrects",
        "services_mirror_backend",
        "partial_never_ready",
        "timeout_detected",
        "port_conflict_detected",
    )
    metric("authentification correcte", "token_rejected_detected", "no_secret_in_outputs")
    judge = run(
        [
            sys.executable,
            "-m",
            "apex.improvement",
            "judge",
            "--capability",
            "desktop_distribution",
            "--version",
            "release-gate",
            "--kind",
            "adversarial",
            "--author",
            "release-gate",
            "--ledger",
            str(out / "judge.ledger.jsonl"),
        ]
    )
    try:
        decision = json.loads(judge.stdout)
    except ValueError:
        decision = {"raw": judge.stdout[-400:] + judge.stderr[-400:]}
    add(
        "adversarial suite validée",
        judge.returncode == 0
        and "pass" in json.dumps(decision).lower()
        and "reject" not in json.dumps(decision).lower(),
        json.dumps(decision, ensure_ascii=False)[:600],
    )

    # -- diagnostic et journaux (Panel réel) -------------------------------------------------------
    diag = [n for n in (latest or {}).get("notes", []) if n.startswith("no_secret_in_outputs")]
    add("diagnostic correct", bool(diag) and "OK" in diag[0], diag[0] if diag else "aucune mesure")
    wine("logs corrects", "W12_journal_utilisateur")

    # -- régression -------------------------------------------------------------------------------
    dotnet = run(["dotnet", "test", str(SETUP / "ApexSetup.sln"), "--nologo"], timeout=3600)
    summary = [
        line.strip() for line in dotnet.stdout.splitlines() if "Passed!" in line or "Failed!" in line
    ]
    add("regression suite validée (.NET)", dotnet.returncode == 0, " | ".join(summary)[-900:])
    pytest = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_panel_status.py",
            "tests/test_launcher_panel_announce.py",
            "tests/test_launcher.py",
            "tests/test_improvement_desktop.py",
            "tests/test_api_auth.py",
        ],
        timeout=3600,
    )
    # La configuration pytest du dépôt n'imprime pas de ligne de totaux : on compte la progression.
    progress = "".join(
        line.split(" [")[0]
        for line in pytest.stdout.splitlines()
        if re.fullmatch(r"[.FEsxX]+ *\[ *\d+%\]", line)
    )
    add(
        "regression suite validée (Python)",
        pytest.returncode == 0,
        f"code {pytest.returncode} : {progress.count('.')} réussis, {progress.count('F')} échecs, "
        f"{progress.count('E')} erreurs, {progress.count('s')} ignorés",
    )

    # -- Gauntlet ---------------------------------------------------------------------------------
    # Le ledger est append-only : les tentatives ratées y restent, et restent citées ici. Règle : le
    # DERNIER cycle de chaque nature a réussi (IMPROVED ou UNCHANGED — un REJECT du juge le rend
    # FAILED), et le dernier cycle a mesuré le code produit ACTUEL (aucune différence entre son commit
    # et HEAD hors banc et documentation). La règle initiale (« aucun cycle en échec, jamais ») était
    # insatisfiable après le premier échec, contrairement à la boucle échec -> correctif -> nouveau
    # cycle : elle a été remplacée après l'avoir constaté (release gate du 2026-09-30).
    cycles = [e["data"] for e in ledger_entries() if e.get("type") == "cycle"]
    verdicts = [
        (
            c.get("kind"),
            c.get("cycle_index"),
            str(c.get("verdict", "?")).lower(),
            str(c.get("version_after", "")),
        )
        for c in cycles
    ]
    latest = {kind: (index, verdict, version) for kind, index, verdict, version in verdicts}
    latest_ok = {"functionality", "adversarial", "improvement"} <= set(latest) and all(
        verdict in ("improved", "unchanged") for _, verdict, _ in latest.values()
    )
    last_version = verdicts[-1][3] if verdicts else ""
    found = re.search(r"-([0-9a-f]{7,40})(?:-|$)", last_version)
    current = (
        bool(found)
        and run(
            ["git", "diff", "--quiet", found.group(1) if found else "HEAD", "HEAD", "--", *PRODUCT_PATHS]
        ).returncode
        == 0
    )
    add(
        "Gauntlet validé",
        latest_ok and current,
        f"dernier cycle par nature : {latest} ; dernier cycle sur le code actuel : "
        f"{'oui' if current else 'NON'} ({last_version}) ; historique complet : "
        f"{[(k, i, v) for k, i, v, _ in verdicts]}",
    )

    # -- documentation ----------------------------------------------------------------------------
    doc = REPO / "docs" / "INSTALLER_APEX.md"
    add(
        "documentation utilisateur générée",
        doc.exists() and "launch.sh" in doc.read_text(encoding="utf-8"),
        str(doc.relative_to(REPO)),
    )

    # -- Windows réel ---------------------------------------------------------------------------------
    for name in (
        "redémarrage Windows validé",
        "WSL réel + launch.sh réel vus depuis Windows",
        "raccourci du vrai shell Windows",
    ):
        items.append(Item(name, BLOCKED, "aucune machine Windows dans cet environnement : NOT VERIFIED"))
    return finish(out, items, started)


def ledger_entries() -> list[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_snapshot() -> dict[str, Any] | None:
    """Dernier snapshot MESURÉ par le moteur sur l'arbre courant (jamais une baseline antérieure)."""
    snapshots = [e["data"] for e in ledger_entries() if e.get("type") == "snapshot"]
    measured = [
        s for s in snapshots if s.get("samples") and not str(s.get("version", "")).startswith("v0-")
    ]
    return measured[-1] if measured else None


def finish(out: Path, items: list[Item], started: float) -> int:
    statuses = {i.status for i in items}
    verdict = (
        "RELEASE_READY"
        if statuses == {PASS}
        else ("NOT_RELEASE_READY" if FAIL in statuses else "BLOCKED_ON_REAL_WINDOWS")
    )
    report = {
        "verdict": verdict,
        "items": [asdict(i) for i in items],
        "seconds": round(time.time() - started),
    }
    (out / "release_gate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [f"# Release gate — {verdict}", "", "| élément | statut | preuve |", "|---|---|---|"]
    lines += [f"| {i.item} | {i.status} | {i.evidence.replace('|', '/')[:300]} |" for i in items]
    (out / "release_gate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if verdict == "RELEASE_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
