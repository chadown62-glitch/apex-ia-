"""`scripts/apex-launch.sh serve` annonce au Panel un démarrage EN COURS (phase `starting`) dès que
Python est trouvé, et le retire si le préflight échoue.

Intégration réelle : le VRAI launch.sh (copié), le VRAI interpréteur du dépôt et le VRAI
`python -m apex.discovery` ; seul `docker` est un double (celui de `tests/test_launcher.py`), qui
photographie l'enregistrement au moment de `compose up` puis échoue.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_launcher import LauncherFixture

REPO = Path(__file__).resolve().parent.parent


def _fixture(tmp_path: Path, compose_up_exit: int) -> tuple[LauncherFixture, Path, Path]:
    fx = LauncherFixture(tmp_path)
    fx.write_env("FOO=bar\n")
    venv_bin = fx.root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(sys.executable)
    (fx.root / "apex").symlink_to(REPO / "apex")
    fx.write_fake_docker(compose_up_exit=compose_up_exit, bind_declared_ports=False)
    discovery = fx.root / ".apex" / "runtime" / "backend.json"
    seen = fx.root / "seen.json"
    docker = fx.bin_dir / "docker"
    docker.write_text(
        docker.read_text().replace(
            "#!/usr/bin/env bash\n",
            "#!/usr/bin/env bash\n"
            f'if [[ "$1 $2" == "compose up" && -f "{discovery}" ]]; then\n'
            f'  printf "%s" "$(<"{discovery}")" > "{seen}"\n'
            "fi\n",
            1,
        )
    )
    return fx, discovery, seen


def _serve(fx: LauncherFixture, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(fx.launcher), "serve", *args],
        cwd=str(fx.root),
        env={"PATH": str(fx.bin_dir), "HOME": str(fx.root)},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_serve_announces_starting_during_preflight_and_reports_the_refusal(tmp_path: Path) -> None:
    fx, discovery, seen = _fixture(tmp_path, compose_up_exit=1)

    result = _serve(fx, "--port", "8123")

    assert result.returncode == 2  # compose up en échec : code documenté du launcher
    assert "démarrage annoncé au Panel" in result.stderr
    record = json.loads(seen.read_text())
    assert record["phase"] == "starting" and record["port"] == 8123
    assert record["project_root"] == str(fx.root.resolve())
    # Exactement ces champs : un chemin relatif vers le jeton, jamais une valeur secrète.
    assert set(record) == {
        "schema",
        "service",
        "phase",
        "apex_version",
        "api_version",
        "panel_protocol",
        "project_root",
        "token_file",
        "host",
        "port",
        "pid",
        "started_at",
    }
    assert record["token_file"] == ".apex/api-token"
    # Le démarrage n'a pas eu lieu : l'annonce devient la raison du refus, lue par le Panel.
    refused = json.loads(discovery.read_text())
    assert refused["phase"] == "refused" and refused["pid"] == record["pid"]
    assert refused["started_at"] == record["started_at"]
    assert refused["refusal"]["exit_code"] == 2 and refused["refusal"]["stage"] == "infrastructure"
    assert refused["refusal"]["blocking"] == [] and refused["refusal"]["unverified_blocking"] == []


def test_port_given_as_equals_form_is_announced(tmp_path: Path) -> None:
    fx, _discovery, seen = _fixture(tmp_path, compose_up_exit=1)

    _serve(fx, "--port=9001", "--host=0.0.0.0")

    record = json.loads(seen.read_text())
    assert (record["port"], record["host"]) == (9001, "0.0.0.0")


def test_default_launcher_mode_never_announces(tmp_path: Path) -> None:
    """Sans `serve`, le launcher ne fait que le préflight : aucune annonce au Panel."""
    fx, discovery, seen = _fixture(tmp_path, compose_up_exit=1)

    fx.run()

    assert not seen.exists() and not discovery.exists()


@pytest.mark.parametrize("bad_port", ["abc", "70000x"])
def test_an_unusable_port_never_blocks_the_launch(tmp_path: Path, bad_port: str) -> None:
    fx, discovery, _seen = _fixture(tmp_path, compose_up_exit=1)

    result = _serve(fx, "--port", bad_port)

    assert result.returncode == 2  # le préflight a continué jusqu'à compose (échec voulu du double)
    assert "annonce du démarrage au Panel impossible" in result.stderr
    assert not discovery.exists()
