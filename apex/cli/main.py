"""CLI APEX.

Phase 1 : `doctor`, `version`, `interfaces`, `config`. Les autres commandes
prévues en §52 apparaîtront à la phase où leur sous-système existe. Une
commande n'est jamais déclarée avant d'exister — `apex --help` est donc une
représentation honnête de ce que le système sait faire aujourd'hui.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from apex.config.dotenv import apply_dotenv
from apex.errors import ApexError
from apex.interfaces.registry import ABSTRACTIONS, ImplStatus, summary
from apex.obs.logging import configure, get_logger
from apex.version import PHASE, PHASE_NAME, __version__, build_info

log = get_logger("cli")


def _cmd_db(args: argparse.Namespace) -> int:
    """`apex db status` / `apex db upgrade` — schéma de la base (apex.db.schema). Aucune valeur secrète
    n'est jamais affichée : seulement des révisions et des noms d'erreurs. Codes : 0 à jour (ou mis à
    jour), 1 échec (connexion, migration), 2 (status seulement) schéma absent ou en retard."""
    from apex.db import schema

    try:
        engine = schema.engine_from_environment()
        if args.db_command == "upgrade":
            before = schema.schema_state(engine)
            if not before.ok:
                print(f"apex db : {before.describe()} — application des migrations…", file=sys.stderr)
                schema.upgrade_to_head()
            after = schema.schema_state(engine)
        else:
            after = schema.schema_state(engine)
    except Exception as exc:  # connexion refusée, migration en échec : jamais une pile brute ni un secret
        detail = str(exc).splitlines()[0][:300] if str(exc) else ""
        print(f"apex db : échec ({exc.__class__.__name__}) : {detail}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"status": after.status.value, "current": after.current, "head": after.head}))
    else:
        print(f"apex db : {after.describe()}")
    if args.db_command == "upgrade":
        return 0 if after.ok else 1
    return 0 if after.ok else 2


def _cmd_version(args: argparse.Namespace) -> int:
    info = build_info()
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(f"APEX {info['version']} — phase {info['phase']} : {info['phase_name']}")
        print(f"Python {info['python']} sur {info['platform']}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from apex.doctor.runner import ALL_CHECKS, FULL_CHECKS, build_context, render_json, render_text, run_checks

    ctx = build_context(config_path=args.config, timeout_s=args.timeout)
    # `--full` (N+26) ajoute `sandbox.CHECKS` (sonde EMPIRIQUE du backend
    # Sandbox réellement sélectionnable — coûteuse, voir sa docstring) au
    # diagnostic rapide par défaut. `apex doctor` seul reste INCHANGÉ :
    # même jeu de checks, même comportement qu'avant cette mission.
    report = run_checks(ctx, checks=FULL_CHECKS if args.full else ALL_CHECKS)
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



#: Codes de sortie d'`apex gate --startup` — distincts de 1 (exception Python non rattrapée) et de 2
#: (erreur d'arguments argparse), pour qu'un lanceur ne confonde jamais un refus avec une panne du Gate.
GATE_STARTUP_BLOCKED = 3
GATE_STARTUP_UNVERIFIED = 4


def _startup_exit_code(report: Any) -> int:
    if report.startup_authorized:
        return 0
    return GATE_STARTUP_BLOCKED if report.startup_blocking else GATE_STARTUP_UNVERIFIED


def _cmd_gate(args: argparse.Namespace) -> int:
    """Hardware & Environment Gate — phase 1.

    Sans `--startup` : code de sortie = autorisation de la phase 2 (inchangé). Avec : décision de
    démarrage d'APEX (`EnvironmentReport.startup_authorized`), même rapport, même affichage."""
    from apex.gate.report import DEFAULT_REPORT_PATH
    from apex.gate.runner import render_text, run_gate

    report = run_gate(
        config_path=args.config,
        exceptions_path=args.exceptions,
        allow_container_exec=args.allow_container_exec,
        allow_network_probe=args.allow_network_probe,
        allow_desktop_control=args.allow_desktop_control,
        timeout_s=args.timeout,
    )
    written = report.write(args.output or DEFAULT_REPORT_PATH)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(report, verbose=args.verbose))
        print(f"  Rapport machine-lisible : {written}\n")
    if getattr(args, "startup", False):
        return _startup_exit_code(report)
    return 0 if report.phase_2_authorized else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    """Démarre l'API HTTP (`apex/api/app.py`) — N+15, la commande derrière
    « démarrage d'APEX » exigée par le launcher de production.

    Vérifie Gate elle-même, inconditionnellement, AVANT de démarrer :
    Gate reste seul juge de l'autorisation (principe déjà établi par
    `_cmd_gate`) et ce serait le contourner que de ne s'y fier que lorsque
    l'appel transite par `scripts/apex-launch.sh` — un `apex serve` invoqué
    directement (sans passer par le launcher) doit refuser exactement de la
    même façon. Aucune option n'existe pour sauter cette vérification.

    La décision est `EnvironmentReport.startup_authorized` (audit du
    02/10/2026), pas `phase_2_authorized` : celle-ci exige des sondes à
    consentement que ni ce code ni le launcher n'accordent jamais, et
    aucune machine ne pouvait démarrer sans dérogation périssable.
    Voir `apex.gate.report.StartupRole` et docs/PHASE_1_GATE.md §10.

    `allow_container_exec`/`allow_network_probe` : lus sur `args` avec
    `False` par défaut — jamais activés d'eux-mêmes. `apex serve` invoqué
    directement ne déclare pas ces attributs (son propre parser ne les
    expose pas) : `getattr(..., False)` y retombe donc exactement sur le
    comportement historique. `_cmd_start` réutilise ce même objet `args`
    pour appeler `_cmd_serve` — les autorisations explicites que
    l'utilisateur a passées à `apex start` (déjà vérifiées une première
    fois par `run_gate` via `run_preflight_and_repair`) sont ainsi
    honorées ICI aussi, au lieu d'être silencieusement retombées à `False`
    sur ce second appel à Gate.
    """
    from apex.gate.report import DEFAULT_REPORT_PATH
    from apex.gate.runner import run_gate

    report = run_gate(
        config_path=args.config,
        exceptions_path="gate.exceptions.yaml",
        allow_container_exec=getattr(args, "allow_container_exec", False),
        allow_network_probe=getattr(args, "allow_network_probe", False),
        allow_desktop_control=getattr(args, "allow_desktop_control", False),
        timeout_s=20.0,
    )
    report.write(DEFAULT_REPORT_PATH)
    # Décision de DÉMARRAGE (EnvironmentReport.startup_authorized), pas l'autorisation de la phase 2 :
    # un FAIL mesuré ou une précondition non vérifiée refuse ; une sonde à consentement non accordée ou
    # une capacité facultative absente (GPU) est « non vérifiée », affichée, jamais un échec.
    if not report.startup_authorized:
        from apex.discovery import write_refused

        # L'annonce « en démarrage » de launch.sh (même pid) devient la raison du refus, lue par le Panel.
        try:
            write_refused(
                args.host, args.port, Path.cwd().resolve(),
                exit_code=5 if report.startup_blocking else 6,
                blocking=[c.check_id for c in report.startup_blocking],
                unverified_blocking=[c.check_id for c in report.startup_unverified_blocking],
            )
        except OSError:
            pass
        print("erreur : apex gate refuse le démarrage — APEX ne démarre pas.", file=sys.stderr)
        if report.startup_blocking:
            print("  FAIL mesurés : " + ", ".join(c.check_id for c in report.startup_blocking), file=sys.stderr)
        if report.startup_unverified_blocking:
            print("  préconditions non vérifiées : "
                  + ", ".join(c.check_id for c in report.startup_unverified_blocking), file=sys.stderr)
        if not report.probe_is_real:
            print("  mesures non issues d'une sonde système réelle", file=sys.stderr)
        print(f"  rapport machine-lisible : {DEFAULT_REPORT_PATH}", file=sys.stderr)
        return 1
    if report.startup_unverified:
        unverified = ", ".join(f"{c.check_id} ({c.unknown_reason})" for c in report.startup_unverified)
        print(f"apex serve : contrôles non vérifiés (n'empêchent pas le démarrage) : {unverified}", file=sys.stderr)
        log.warning("contrôles du Gate non vérifiés au démarrage", checks=unverified,
                    phase_2_authorized=report.phase_2_authorized)

    try:
        import uvicorn
    except ImportError:
        print(
            "erreur : uvicorn n'est pas installé — installez l'extra optionnel "
            "`api` (pip install -e '.[api]') avant d'utiliser `apex serve`.",
            file=sys.stderr,
        )
        return 1

    from apex.api.app import create_app
    from apex.discovery import cleanup_on_shutdown, remove_discovery, write_discovery

    log.info("démarrage d'APEX (apex serve)", host=args.host, port=args.port)
    # Découverte par le Panel Windows (`APEX.exe`) : phase `serving` écrite AVANT l'écoute (le
    # Panel voit « en démarrage » jusqu'à la première réponse), retirée à l'arrêt. Aucun secret
    # dedans — voir apex/discovery.py.
    try:
        discovery = write_discovery(args.host, args.port, Path.cwd().resolve(), phase="serving")
        log.info("enregistrement de découverte écrit", path=str(discovery))
    except OSError as exc:
        log.warning("découverte impossible — le Panel ne trouvera pas ce backend", error=str(exc))
    from apex.obs.component_checks import warm_up

    warm_up()
    try:
        uvicorn.run(
            cleanup_on_shutdown(create_app()),
            host=args.host,
            port=args.port,
            log_level=(args.log_level or "info").lower(),
        )
    finally:
        remove_discovery()
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    """`apex models status` : chaque endpoint déclaré et ce que son backend constate RÉELLEMENT (`detect`).
    `apex models fetch` : place les poids d'un modèle natif (action explicite, réseau)."""
    from pathlib import Path

    if args.models_cmd == "fetch":
        from apex.models.fetch import default_models_dir, fetch_model

        models_dir = Path(args.dir) if args.dir else default_models_dir()
        if models_dir is None:
            print("aucun endpoint natif (runtime: transformers) dans la configuration — préciser --dir")
            return 2
        path = fetch_model(args.repo, models_dir=models_dir, name=args.name, revision=args.revision)
        print(f"poids placés dans {path}")
        return 0
    from apex.config.loader import load_config
    from apex.models.provider import get_default_provider

    for endpoint in load_config().endpoints:
        if not endpoint.enabled:
            print(f"{endpoint.runtime:<14} {endpoint.served_model:<40} désactivé")
            continue
        report = get_default_provider(endpoint).detect()
        print(f"{endpoint.runtime:<14} {endpoint.served_model:<40} {report.status.value:<20} {report.detail}")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    """Launcher auto-réparant — N+19, AXE B : UN SEUL point d'entrée
    DIAGNOSTIC -> AUTO-REPAIR -> VERIFY -> GATE -> START. Réutilise
    intégralement `apex doctor` (lecture seule, inchangé) et `apex gate`
    (jamais court-circuitée, même discipline que `_cmd_serve`) — ce module
    n'ajoute que la couche de classification/réparation
    (`apex.doctor.repair`) entre les deux."""
    from apex.doctor.launcher import StartupStatus, render_text, run_preflight_and_repair
    from apex.doctor.startup_history import DEFAULT_HISTORY_PATH

    # Les mêmes autorisations explicites qu'`apex gate --allow-...` —
    # jamais activées par défaut (`args.allow_*` reste `False` sans flag,
    # même discipline que `gate_cmd`). Transmises telles quelles à
    # `run_gate` via `gate_kwargs` (`run_preflight_and_repair` ne fait que
    # les relayer, jamais une seconde politique d'autorisation).
    gate_kwargs = {
        "allow_container_exec": args.allow_container_exec,
        "allow_network_probe": args.allow_network_probe,
        "allow_desktop_control": args.allow_desktop_control,
    }
    outcome = run_preflight_and_repair(
        config_path=args.config, dry_run=args.dry_run, timeout_s=args.timeout,
        history_path=DEFAULT_HISTORY_PATH, gate_kwargs=gate_kwargs,
    )
    print(render_text(outcome, verbose=args.verbose))

    if outcome.status is StartupStatus.DRY_RUN:
        return 0
    if outcome.status is StartupStatus.BLOCKED_DOCTOR:
        print("erreur : environnement non sain après réparation — APEX ne démarre pas.", file=sys.stderr)
        return 1
    if outcome.status is StartupStatus.BLOCKED_GATE:
        print("erreur : apex gate refuse l'autorisation — APEX ne démarre pas.", file=sys.stderr)
        return 1

    # STARTUP_STATUS.READY_TO_SERVE : démarrage réel, exactement le même
    # chemin que `apex serve` (aucun second serveur, aucune duplication).
    args.host = getattr(args, "host", "127.0.0.1")
    args.port = getattr(args, "port", 8000)
    return _cmd_serve(args)


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
    doctor_cmd.add_argument(
        "--full", action="store_true",
        help="ajoute la sonde d'isolation sandbox (coûteuse : namespaces/cgroups/Docker réels)",
    )
    doctor_cmd.set_defaults(func=_cmd_doctor)

    db_cmd = sub.add_parser("db", help="schéma de la base : état ou mise à niveau (migrations Alembic)")
    db_sub = db_cmd.add_subparsers(dest="db_command", required=True)
    for name, help_text in (("status", "révision appliquée contre révision attendue par ce code"),
                            ("upgrade", "appliquer les migrations manquantes (vers l'avant, idempotent)")):
        db_one = db_sub.add_parser(name, help=help_text)
        db_one.add_argument("--json", action="store_true")
        db_one.set_defaults(func=_cmd_db)

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
    gate_cmd.add_argument(
        "--allow-desktop-control", action="store_true",
        help="autoriser la sonde de contrôle clavier/souris (GATE-22) — jamais d'action réelle, "
             "seulement une mesure de capacité",
    )
    gate_cmd.add_argument(
        "--startup", action="store_true",
        help="code de sortie = décision de DÉMARRAGE d'APEX (0 autorisé, "
             f"{GATE_STARTUP_BLOCKED} FAIL mesuré, {GATE_STARTUP_UNVERIFIED} précondition non vérifiée) "
             "au lieu de l'autorisation de phase 2 — utilisé par scripts/apex-launch.sh",
    )
    gate_cmd.set_defaults(func=_cmd_gate)

    serve_cmd = sub.add_parser(
        "serve", help="démarrer l'API HTTP (vérifie Gate elle-même avant de démarrer)"
    )
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.set_defaults(func=_cmd_serve)

    start_cmd = sub.add_parser(
        "start",
        help="launcher auto-réparant : diagnostic -> réparation sûre -> vérification -> gate -> démarrage",
    )
    start_cmd.add_argument("--host", default="127.0.0.1")
    start_cmd.add_argument("--port", type=int, default=8000)
    start_cmd.add_argument("--timeout", type=float, default=5.0, help="timeout par vérification (s)")
    start_cmd.add_argument(
        "--dry-run", action="store_true",
        help="diagnostiquer et afficher les réparations sans les exécuter, jamais démarrer",
    )
    start_cmd.add_argument("-v", "--verbose", action="store_true")
    start_cmd.add_argument(
        "--allow-container-exec", action="store_true",
        help="autoriser le Gate à démarrer des conteneurs de test (GATE-17, 18, 20)",
    )
    start_cmd.add_argument(
        "--allow-network-probe", action="store_true",
        help="autoriser une sonde de sortie réseau (GATE-15)",
    )
    start_cmd.add_argument(
        "--allow-desktop-control", action="store_true",
        help="autoriser la sonde de contrôle clavier/souris (GATE-22) — jamais d'action réelle, "
             "seulement une mesure de capacité",
    )
    start_cmd.set_defaults(func=_cmd_start)

    models_cmd = sub.add_parser("models", help="modèles : état réel des endpoints, placement des poids natifs")
    models_sub = models_cmd.add_subparsers(dest="models_cmd", required=True)
    models_sub.add_parser("status", help="ce que chaque backend constate réellement (detect)")
    fetch_cmd = models_sub.add_parser("fetch", help="télécharger les poids d'un modèle natif (réseau, explicite)")
    fetch_cmd.add_argument("repo", help="dépôt Hugging Face, ex. Qwen/Qwen2.5-Coder-1.5B-Instruct")
    fetch_cmd.add_argument("--name", required=True, help="nom servi (= served_model de l'endpoint)")
    fetch_cmd.add_argument("--dir", default=None, help="répertoire des modèles (défaut : celui de la config)")
    fetch_cmd.add_argument("--revision", default=None)
    models_cmd.set_defaults(func=_cmd_models)

    # Commandes de RECHERCHE (`learn`, `learning`, `solve`) : ajoutées par l'extension `apex.research_api`
    # seulement quand elle est présente — jamais déclarées dans un Runtime qui ne les contient pas.
    from apex.util.extensions import load_research_extension

    research = load_research_extension()
    if research is not None:
        research.register_cli(sub)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Charge `.env` dans `os.environ` AVANT tout `load_config()` (fait par
    # chaque sous-commande) — cause racine d'un décalage réel diagnostiqué :
    # sans cet appel, `.env` reste un fichier inerte pour ce process Python
    # (seul `docker compose` le charge, pour les conteneurs, automatiquement
    # — voir la docstring d'`apex.config.dotenv`). `os.environ.get(
    # "APEX_ENV_FILE", ".env")` reste résolu AVANT ce chargement : une valeur
    # réellement exportée par le shell prime toujours sur le fichier.
    apply_dotenv(os.environ.get("APEX_ENV_FILE", ".env"))

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
