"""Commandes CLI de RECHERCHE (`apex learn`, `apex learning`, `apex solve`) — retirées de `apex.cli.main`
(runtime) le 06/10/2026, corps inchangés. Ajoutées au parseur par `register` quand l'extension est présente."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from apex.obs.logging import get_logger

log = get_logger("cli")


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

    from apex.research_api.dependencies import get_learning_service

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
    from apex.research_api.dependencies import get_learning_service

    try:
        status = get_learning_service().status()
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_cycle_status(status, as_json=args.json))
    return 0


def _cmd_learn_history(args: argparse.Namespace) -> int:
    from apex.research_api.dependencies import get_learning_service

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
    from apex.research_api.dependencies import get_learning_service

    try:
        status = get_learning_service().cancel()
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_cycle_status(status, as_json=args.json))
    return 0


def _format_learning_session(session: Any, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "id": session.id, "specialty": session.specialty, "status": session.status.value,
                "max_generations": session.max_generations, "generations_completed": session.generations_completed,
                "convergence_patience": session.convergence_patience,
                "generations_since_improvement": session.generations_since_improvement,
                "best_member_id": session.best_member_id, "best_fitness": session.best_fitness,
                "best_genome_version_id": session.best_genome_version_id, "stop_reason": session.stop_reason,
                "error": session.error, "created_by": session.created_by,
                "created_at": session.created_at.isoformat(),
                "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            },
            indent=2, ensure_ascii=False,
        )
    lines = [
        f"session : {session.id}", f"spécialité : {session.specialty}", f"état : {session.status.value}",
        f"générations : {session.generations_completed}/{session.max_generations}",
    ]
    if session.best_fitness is not None:
        lines.append(f"meilleur fitness : {session.best_fitness} (membre {session.best_member_id})")
    if session.stop_reason:
        lines.append(f"raison d'arrêt : {session.stop_reason}")
    if session.error:
        lines.append(f"erreur : {session.error}")
    return "\n".join(lines)


def _cmd_learning_start(args: argparse.Namespace) -> int:
    """Démarre réellement une session d'apprentissage multi-génération via
    l'UNIQUE orchestrateur autoritaire `apex.metalearner.learning_
    orchestrator.LearningOrchestrator` (même service que `POST
    /learning-sessions` — aucune logique de boucle dupliquée ici). Rend la
    main IMMÉDIATEMENT (`RUNNING`) : la boucle multi-génération continue en
    tâche de fond dans le processus API/CLI qui l'a démarrée — cette
    commande n'attend PAS sa fin (contrairement à `apex learn start`, qui
    enveloppe un unique cycle court) ; utiliser `apex learning status` pour
    suivre sa progression durable (DB), depuis n'importe quel processus."""
    from apex.errors import LearningSessionAlreadyRunning
    from apex.specialties.registry import by_id

    if by_id(args.specialty) is None:
        print(f"erreur : spécialité « {args.specialty} » inconnue", file=sys.stderr)
        return 1

    from apex.research_api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().start_session(
            args.specialty, max_generations=args.generations, convergence_patience=args.convergence_patience,
        )
    except LearningSessionAlreadyRunning as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_learning_session(session, as_json=args.json))
    return 0


def _cmd_learning_status(args: argparse.Namespace) -> int:
    from apex.research_api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().get_session(args.session_id)
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    if session is None:
        print(f"erreur : session « {args.session_id} » introuvable", file=sys.stderr)
        return 1
    print(_format_learning_session(session, as_json=args.json))
    return 0


def _cmd_learning_list(args: argparse.Namespace) -> int:
    from apex.research_api.dependencies import get_learning_orchestrator

    try:
        sessions = get_learning_orchestrator().list_sessions(specialty=args.specialty, limit=args.limit)
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([json.loads(_format_learning_session(s, as_json=True)) for s in sessions], indent=2, ensure_ascii=False))
        return 0
    if not sessions:
        print("aucune session d'apprentissage.")
        return 0
    for s in sessions:
        print(
            f"{s.created_at.isoformat()}  {s.status.value:<16}  {s.id}  spécialité={s.specialty}  "
            f"générations={s.generations_completed}/{s.max_generations}"
        )
    return 0


def _cmd_learning_resume(args: argparse.Namespace) -> int:
    """Reprend une session ARRÊTÉE (`failed`/`cancelled`) à la génération
    suivant la dernière réellement complétée — jamais depuis 0 (voir la
    docstring de `LearningOrchestrator` : `evolve_generation` dérive
    toujours son numéro de la population déjà persistée)."""
    from apex.errors import LearningSessionAlreadyRunning

    from apex.research_api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().resume_session(args.session_id)
    except LookupError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except LearningSessionAlreadyRunning as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return exc.exit_code
    except ValueError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_learning_session(session, as_json=args.json))
    return 0


def _cmd_learning_cancel(args: argparse.Namespace) -> int:
    """No-op si la session n'est pas `running` — jamais une erreur pour un
    appel redondant (même patron que `POST /learning-sessions/{id}/cancel`).
    Une génération déjà en cours va toujours à son terme."""
    from apex.research_api.dependencies import get_learning_orchestrator

    try:
        session = get_learning_orchestrator().cancel_session(args.session_id)
    except LookupError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print(_format_learning_session(session, as_json=args.json))
    return 0


def _cmd_solve(args: argparse.Namespace) -> int:
    """Boucle adaptative G0 -> G1 -> ... sur une tâche à `solution_harness`
    (`apex.metalearner.solution_loop_runner`) — code réellement généré,
    exécuté dans le sandbox, jugé, mémorisé ; la décision de chaque
    génération dérive de l'expérience de la précédente."""
    import json

    from apex.research_api.dependencies import get_session_factory
    from apex.config.loader import load_config
    from apex.metalearner.solution_loop_runner import (
        SolutionLoopRequest,
        SolutionLoopUnavailable,
        run_solution_loop,
        summarize,
    )

    request = SolutionLoopRequest(
        task_id=args.task_id, role=args.role, generations=args.generations, candidates=args.candidates,
        base_temperature=args.base_temperature,
        target_fitness=None if args.no_target else args.target_fitness,
        experiment_ref=args.experiment_ref,
    )
    try:
        result = run_solution_loop(get_session_factory(), load_config(args.config), request)
    except (SolutionLoopUnavailable, LookupError, ValueError) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"erreur : {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    summary = summarize(result)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"expérience : {summary['experiment_ref']} — arrêt : {summary['stop_reason']}")
        for generation in summary["generations"]:
            print(f"G{generation['generation']} meilleur={generation['best_fitness']:.3f} "
                  f"({generation['best_category']}) — {generation['decision']}")
        print(f"meilleur : G{summary['best_generation']} verdict={summary['best_verdict']}")
    return 0 if summary["best_verdict"] == "pass" else 1


def register(sub: Any) -> None:
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

    learning_cmd = sub.add_parser(
        "learning",
        help="session d'apprentissage MULTI-génération (apex.metalearner.learning_orchestrator)",
    )
    learning_sub = learning_cmd.add_subparsers(dest="learning_command", required=True)

    learning_start_cmd = learning_sub.add_parser("start", help="démarrer une session (rend la main immédiatement)")
    learning_start_cmd.add_argument("--specialty", required=True, help="ex. « coding »")
    learning_start_cmd.add_argument("--generations", type=int, required=True, help="nombre maximal de générations")
    learning_start_cmd.add_argument(
        "--convergence-patience", type=int, default=0,
        help="arrêt anticipé après N générations sans amélioration (0 = désactivé)",
    )
    learning_start_cmd.add_argument("--json", action="store_true")
    learning_start_cmd.set_defaults(func=_cmd_learning_start)

    learning_status_cmd = learning_sub.add_parser("status", help="état durable d'une session (par id)")
    learning_status_cmd.add_argument("session_id")
    learning_status_cmd.add_argument("--json", action="store_true")
    learning_status_cmd.set_defaults(func=_cmd_learning_status)

    learning_list_cmd = learning_sub.add_parser("list", help="sessions passées/en cours (durable)")
    learning_list_cmd.add_argument("--specialty", default=None)
    learning_list_cmd.add_argument("--limit", type=int, default=20)
    learning_list_cmd.add_argument("--json", action="store_true")
    learning_list_cmd.set_defaults(func=_cmd_learning_list)

    learning_resume_cmd = learning_sub.add_parser("resume", help="reprendre une session arrêtée")
    learning_resume_cmd.add_argument("session_id")
    learning_resume_cmd.add_argument("--json", action="store_true")
    learning_resume_cmd.set_defaults(func=_cmd_learning_resume)

    learning_cancel_cmd = learning_sub.add_parser("cancel", help="demander l'annulation (effective entre deux générations)")
    learning_cancel_cmd.add_argument("session_id")
    learning_cancel_cmd.add_argument("--json", action="store_true")
    learning_cancel_cmd.set_defaults(func=_cmd_learning_cancel)

    solve_cmd = sub.add_parser(
        "solve", help="boucle adaptative générer -> exécuter (sandbox) -> juger -> apprendre sur une tâche",
    )
    solve_cmd.add_argument("task_id", help="tâche portant un solution_harness")
    solve_cmd.add_argument("--role", default="coder_main", help="rôle modèle de config.yaml")
    solve_cmd.add_argument("--generations", type=int, default=3)
    solve_cmd.add_argument("--candidates", type=int, default=2)
    solve_cmd.add_argument("--base-temperature", type=float, default=0.7)
    solve_cmd.add_argument("--target-fitness", type=float, default=1.0)
    solve_cmd.add_argument("--no-target", action="store_true", help="aller jusqu'à --generations")
    solve_cmd.add_argument("--experiment-ref", default=None)
    solve_cmd.add_argument("--json", action="store_true")
    solve_cmd.set_defaults(func=_cmd_solve)
