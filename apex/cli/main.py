"""CLI APEX.

Phase 1 : `doctor`, `version`, `interfaces`, `config`. Les autres commandes
prévues en §52 apparaîtront à la phase où leur sous-système existe. Une
commande n'est jamais déclarée avant d'exister — `apex --help` est donc une
représentation honnête de ce que le système sait faire aujourd'hui.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from apex.errors import ApexError
from apex.interfaces.registry import ABSTRACTIONS, ImplStatus, summary
from apex.obs.logging import configure, get_logger
from apex.version import PHASE, PHASE_NAME, __version__, build_info

log = get_logger("cli")


def _cmd_version(args: argparse.Namespace) -> int:
    info = build_info()
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(f"APEX {info['version']} — phase {info['phase']} : {info['phase_name']}")
        print(f"Python {info['python']} sur {info['platform']}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from apex.doctor.runner import build_context, render_json, render_text, run_checks

    ctx = build_context(config_path=args.config, timeout_s=args.timeout)
    report = run_checks(ctx)
    print(render_json(report) if args.json else render_text(report, verbose=args.verbose))
    return report.exit_code


def _cmd_interfaces(args: argparse.Namespace) -> int:
    """État réel des abstractions — répond directement au principe 15."""
    items = sorted(ABSTRACTIONS, key=lambda a: (a.phase_implemented, a.name))
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary(),
                    "abstractions": [
                        {
                            "name": a.name,
                            "purpose": a.purpose,
                            "status": a.status.value,
                            "phase_defined": a.phase_defined,
                            "phase_implemented": a.phase_implemented,
                            "fallback": a.fallback,
                            "module": a.module,
                            "notes": a.notes,
                        }
                        for a in items
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    counts = summary()
    print(f"\n{len(ABSTRACTIONS)} abstractions — "
          f"{counts[ImplStatus.IMPLEMENTED.value]} implémentées, "
          f"{counts[ImplStatus.STUB.value]} stubs, "
          f"{counts[ImplStatus.DEFINED.value]} définies, "
          f"{counts[ImplStatus.PLANNED.value]} planifiées\n")
    print(f"{'ABSTRACTION':<22}{'ÉTAT':<14}{'DÉF.':>5}{'IMPL.':>7}   REPLI")
    print("-" * 88)
    for item in items:
        print(
            f"{item.name:<22}{item.status.value:<14}"
            f"{item.phase_defined:>5}{item.phase_implemented:>7}   {item.fallback}"
        )
    print()
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    from apex.config.loader import load_config

    config = load_config(args.config)
    payload = {
        "version": config.version,
        "phase": config.phase,
        "pools": {
            pid: {
                "location": pool.location.value,
                "trust_level": pool.trust_level.value,
                "vram_gb": pool.total_vram_gb,
                "capabilities": [c.value for c in pool.capabilities],
            }
            for pid, pool in config.pools.items()
        },
        "model_roles": {
            name: {
                "reference": role.reference.id,
                "candidates": [c.id for c in role.candidates],
                "selection": role.selection,
                "resident": role.resident,
            }
            for name, role in config.model_roles.items()
        },
        "external": {
            "enabled": config.external.enabled,
            "allowed_contexts": sorted(config.external.allowed_contexts),
        },
        "budget": {
            "nightly_gpu_minutes": config.budget.nightly_gpu_minutes,
            "max_experiment_gpu_minutes": config.budget.max_experiment_gpu_minutes,
        },
        "languages": {
            name: {"wave": lang.wave, "oracle_strength": lang.oracle_strength,
                   "separate_metrics": lang.separate_metrics}
            for name, lang in config.languages.items()
        },
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0



def _cmd_gate(args: argparse.Namespace) -> int:
    """Hardware & Environment Gate — phase 1."""
    from apex.gate.report import DEFAULT_REPORT_PATH
    from apex.gate.runner import render_text, run_gate

    report = run_gate(
        config_path=args.config,
        exceptions_path=args.exceptions,
        allow_container_exec=args.allow_container_exec,
        allow_network_probe=args.allow_network_probe,
        timeout_s=args.timeout,
    )
    written = report.write(args.output or DEFAULT_REPORT_PATH)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(report, verbose=args.verbose))
        print(f"  Rapport machine-lisible : {written}\n")
    return 0 if report.phase_2_authorized else 1


def _format_cycle_status(status: Any, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "state": status.state.value, "cycle_id": status.cycle_id, "specialty": status.specialty,
                "started_at": status.started_at.isoformat() if status.started_at else None,
                "completed_at": status.completed_at.isoformat() if status.completed_at else None,
                "error": status.error, "eval_run_id": status.eval_run_id, "task_id": status.task_id,
                "oracle_verdict": status.oracle_verdict,
            },
            indent=2, ensure_ascii=False,
        )
    lines = [f"état : {status.state.value}"]
    if status.cycle_id:
        lines.append(f"cycle : {status.cycle_id}")
    if status.specialty:
        lines.append(f"spécialité : {status.specialty}")
    if status.eval_run_id:
        lines.append(f"eval_run : {status.eval_run_id} (verdict : {status.oracle_verdict})")
    if status.task_id:
        lines.append(f"tâche : {status.task_id}")
    if status.error:
        lines.append(f"erreur : {status.error}")
    return "\n".join(lines)


def _cmd_learn_start(args: argparse.Namespace) -> int:
    """Démarre réellement un cycle d'apprentissage autonome
    (`apex.metalearner.cycle.run_experiment_cycle`, via l'unique service
    autoritaire `apex.metalearner.autonomy.AutonomousLearningService` —
    aucune logique d'apprentissage dupliquée ici, même chemin que
    `POST /learning/cycles`). `start_cycle` est non bloquant par nature
    (le Scheduler/l'API restent des processus longue durée) ; cette
    commande ATTEND explicitement la fin du cycle avant de rendre la main
    — un processus CLI de courte durée qui se terminerait immédiatement
    tuerait le thread démon avant que le cycle n'ait fini (§ « autonomy »),
    ce que le mode API/Scheduler n'a jamais besoin de faire."""
    import time

    from apex.errors import CycleAlreadyRunning
    from apex.evalrunner.model import EvalMethod
    from apex.metalearner.autonomy import CycleState
    from apex.specialties.registry import by_id
    from apex.tasks.model import TaskSplit

    if by_id(args.specialty) is None:
        print(f"erreur : spécialité « {args.specialty} » inconnue", file=sys.stderr)
        return 1
    try:
        method = EvalMethod(args.method)
    except ValueError:
        print(f"erreur : méthode « {args.method} » inconnue", file=sys.stderr)
        return 1
    try:
        split = TaskSplit(args.split)
    except ValueError:
        print(f"erreur : split « {args.split} » inconnu", file=sys.stderr)
        return 1

    from apex.api.dependencies import get_learning_service

    try:
        service = get_learning_service()
        status = service.start_cycle(
            specialty=args.specialty, current_level=args.current_level,
            method=method, split=split, clone_id=args.clone_id,
        )
    except CycleAlreadyRunning as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    # Ctrl+C annule réellement le cycle en cours (`CancellationToken.
    # request_cancel`) plutôt que de simplement tuer ce processus en laissant
    # le thread démon disparaître sans trace — seul point d'entrée CLI où
    # l'annulation est réellement utile : `apex learn stop` lancé depuis un
    # AUTRE processus ne voit jamais ce cycle (état en mémoire, par
    # processus — même limite honnête qu'`apex.scheduler.registry.
    # ExecutionRegistry`).
    deadline = time.monotonic() + args.timeout
    try:
        while status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED) and time.monotonic() < deadline:
            time.sleep(0.2)
            status = service.status()
    except KeyboardInterrupt:
        print("\nannulation demandée (Ctrl+C) — attente de la fin du cycle...", file=sys.stderr)
        status = service.cancel()
        while status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED) and time.monotonic() < deadline:
            time.sleep(0.2)
            status = service.status()

    if status.state in (CycleState.RUNNING, CycleState.CANCEL_REQUESTED):
        print(
            f"attention : le cycle {status.cycle_id} n'a pas terminé sous {args.timeout:.0f}s — "
            "il sera interrompu à la fermeture de ce processus (thread démon).", file=sys.stderr,
        )
        print(_format_cycle_status(status, as_json=args.json))
        return 4

    print(_format_cycle_status(status, as_json=args.json))
    return 0 if status.state is CycleState.COMPLETED else 1


def _cmd_learn_status(args: argparse.Namespace) -> int:
    from apex.api.dependencies import get_learning_service

    try:
        status = get_learning_service().status()
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_cycle_status(status, as_json=args.json))
    return 0


def _cmd_learn_history(args: argparse.Namespace) -> int:
    from apex.api.dependencies import get_learning_service

    try:
        entries = get_learning_service().history(limit=args.limit)
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "cycle_id": e.cycle_id, "specialty": e.specialty, "state": e.state.value,
                        "eval_run_id": e.eval_run_id, "task_id": e.task_id, "oracle_verdict": e.oracle_verdict,
                        "error": e.error, "completed_at": e.completed_at.isoformat(),
                    }
                    for e in entries
                ],
                indent=2, ensure_ascii=False,
            )
        )
        return 0
    if not entries:
        print("aucun cycle d'apprentissage autonome dans l'historique.")
        return 0
    for e in entries:
        verdict = f" verdict={e.oracle_verdict}" if e.oracle_verdict else ""
        error = f" erreur={e.error}" if e.error else ""
        print(f"{e.completed_at.isoformat()}  {e.state.value:<10}  {e.cycle_id}  spécialité={e.specialty}{verdict}{error}")
    return 0


def _cmd_learn_stop(args: argparse.Namespace) -> int:
    """N'a d'effet QUE sur un cycle suivi par CE processus — l'état est en
    mémoire, jamais partagé entre deux invocations séparées de la CLI (même
    limite honnête qu'`apex.scheduler.registry.ExecutionRegistry`). Puisque
    `apex learn start` bloque déjà jusqu'à la fin du cycle dans SON propre
    processus, la façon réellement utile d'annuler depuis la CLI est Ctrl+C
    sur CE processus `start` (voir `_cmd_learn_start`) — cette commande
    reste utile principalement contre le processus API long-vivant
    (`POST /learning/cycles/cancel`, même service)."""
    from apex.api.dependencies import get_learning_service

    try:
        status = get_learning_service().cancel()
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_cycle_status(status, as_json=args.json))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apex",
        description=f"APEX {__version__} — phase {PHASE} : {PHASE_NAME}",
        epilog="Les commandes des phases ultérieures apparaîtront lorsque leur sous-système existera.",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--config", default=None, help="chemin de config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    version_cmd = sub.add_parser("version", help="version et informations de build")
    version_cmd.add_argument("--json", action="store_true")
    version_cmd.set_defaults(func=_cmd_version)

    doctor_cmd = sub.add_parser("doctor", help="diagnostic de l'environnement (lecture seule)")
    doctor_cmd.add_argument("--json", action="store_true")
    doctor_cmd.add_argument("-v", "--verbose", action="store_true", help="afficher toutes les remédiations")
    doctor_cmd.add_argument("--timeout", type=float, default=5.0, help="timeout par vérification (s)")
    doctor_cmd.set_defaults(func=_cmd_doctor)

    interfaces_cmd = sub.add_parser("interfaces", help="état réel des abstractions et de leur implémentation")
    interfaces_cmd.add_argument("--json", action="store_true")
    interfaces_cmd.set_defaults(func=_cmd_interfaces)

    config_cmd = sub.add_parser("config", help="afficher la configuration validée")
    config_cmd.add_argument("--json", action="store_true", default=True)
    config_cmd.set_defaults(func=_cmd_config)

    gate_cmd = sub.add_parser(
        "gate", help="Hardware & Environment Gate : 20 contrôles + rapport machine-lisible"
    )
    gate_cmd.add_argument("--json", action="store_true")
    gate_cmd.add_argument("-v", "--verbose", action="store_true")
    gate_cmd.add_argument("--output", default=None, help="chemin du rapport JSON")
    gate_cmd.add_argument("--exceptions", default="gate.exceptions.yaml")
    gate_cmd.add_argument("--timeout", type=float, default=20.0)
    gate_cmd.add_argument(
        "--allow-container-exec", action="store_true",
        help="autoriser le Gate à démarrer des conteneurs de test (GATE-17, 18, 20)",
    )
    gate_cmd.add_argument(
        "--allow-network-probe", action="store_true",
        help="autoriser une sonde de sortie réseau (GATE-15)",
    )
    gate_cmd.set_defaults(func=_cmd_gate)

    learn_cmd = sub.add_parser(
        "learn", help="cycle d'apprentissage autonome (apex.metalearner.autonomy)"
    )
    learn_sub = learn_cmd.add_subparsers(dest="learn_command", required=True)

    learn_start_cmd = learn_sub.add_parser("start", help="démarrer un cycle et attendre sa fin")
    learn_start_cmd.add_argument("--specialty", required=True, help="ex. « research »")
    learn_start_cmd.add_argument("--current-level", type=int, default=1)
    learn_start_cmd.add_argument("--method", default="practice")
    learn_start_cmd.add_argument("--split", default="train")
    learn_start_cmd.add_argument(
        "--clone-id", default=None,
        help="clone déjà spawné à réutiliser — sinon un clone Subject.LEARNING est provisionné",
    )
    learn_start_cmd.add_argument(
        "--timeout", type=float, default=900.0,
        help="attente maximale (s) de la fin du cycle avant que ce processus ne rende la main",
    )
    learn_start_cmd.add_argument("--json", action="store_true")
    learn_start_cmd.set_defaults(func=_cmd_learn_start)

    learn_status_cmd = learn_sub.add_parser("status", help="état du cycle suivi par ce processus")
    learn_status_cmd.add_argument("--json", action="store_true")
    learn_status_cmd.set_defaults(func=_cmd_learn_status)

    learn_history_cmd = learn_sub.add_parser("history", help="cycles autonomes passés (durable)")
    learn_history_cmd.add_argument("--limit", type=int, default=20)
    learn_history_cmd.add_argument("--json", action="store_true")
    learn_history_cmd.set_defaults(func=_cmd_learn_history)

    learn_stop_cmd = learn_sub.add_parser("stop", help="demander l'annulation du cycle en cours")
    learn_stop_cmd.add_argument("--json", action="store_true")
    learn_stop_cmd.set_defaults(func=_cmd_learn_stop)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure(args.log_level)
    try:
        return int(args.func(args))
    except ApexError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
