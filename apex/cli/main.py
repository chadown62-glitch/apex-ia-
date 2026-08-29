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
from typing import Sequence

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
