"""Launcher-facing diagnostics and explicit provisioning; no implicit downloads."""

from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="APEX Game Studio dependency management")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dependencies", help="Read dependency diagnostics; no downloads")
    install = commands.add_parser("install-planner", help="Explicit pinned 5.4 GB planner download")
    install.add_argument("--accept-download", action="store_true")
    install.add_argument("--accept-apache-2-license", action="store_true")
    args = parser.parse_args(argv)
    try:
        from apex.game_studio.dependencies import install_planner, planner_installation

        if args.command == "dependencies":
            from apex.game_studio.capabilities import probe

            result = {"capabilities": probe(), "planner_installation": planner_installation()}
        else:
            result = install_planner(
                accept_download=args.accept_download, accept_license=args.accept_apache_2_license
            )
    except (ImportError, OSError, RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
