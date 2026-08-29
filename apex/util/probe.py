"""Surface d'effets de bord injectable.

`apex doctor` doit inspecter le système : exécuter des commandes, lire des
fichiers, ouvrir des sockets. Ces opérations sont regroupées derrière un
protocole unique `Probe` pour trois raisons :

1. **Testabilité.** Les vérifications sont testables hors ligne, sans Docker,
   sans GPU, sans PostgreSQL, en injectant `FakeProbe`. C'est ce qui permet à
   la suite de tests de tourner sur n'importe quelle machine, y compris en CI.
2. **Timeout systématique.** Aucune opération à effet de bord n'est possible
   sans budget de temps (principe 13). La signature l'impose.
3. **Innocuité.** `Probe` est en lecture seule : pas d'écriture, pas
   d'installation, pas de modification (§53 — le doctor diagnostique, il ne
   répare pas).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


#: Pages de code essayees apres UTF-8, dans l'ordre.
#:
#: Un binaire Windows appele via l'interop WSL ecrit dans la page de code OEM
#: de la console, pas en UTF-8. En francais c'est CP850 (0x82 = « e accent
#: aigu »), en anglais US CP437, et certains outils utilisent CP1252. Decoder
#: en UTF-8 strict fait alors lever UnicodeDecodeError et fait echouer un
#: controle pour une raison qui n'a rien a voir avec ce qu'il mesure.
OEM_CODEPAGES = ("cp850", "cp437", "cp1252")


def decode_process_output(data: bytes) -> tuple[str, str]:
    """Decode une sortie de processus sans jamais lever d'exception.

    Retourne le texte et le nom de l'encodage retenu. L'encodage est conserve
    et publie dans le resultat : un decodage approximatif doit rester visible,
    pas etre masque. La strategie est deterministe et ordonnee :

    1. UTF-16 si un BOM est present ou si la sortie contient des octets nuls
       (cas de `cmd.exe /u`, qui ecrit en UTF-16LE) ;
    2. UTF-8 strict, cas normal sous Linux ;
    3. les pages de code OEM Windows, dans l'ordre ;
    4. latin-1 en dernier recours : ce codec ne peut pas echouer, ce qui
       garantit qu'aucun appel de sonde ne plante a cause d'un encodage.

    Le dernier recours n'est pas un contournement : il est signale par la
    valeur d'encodage retournee, de sorte qu'un appelant puisse decider que la
    sortie n'est pas exploitable.
    """
    if not data:
        return "", "utf-8"

    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or data.count(b"\x00") > len(data) // 4:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding), encoding
            except (UnicodeDecodeError, ValueError):
                continue

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    for encoding in OEM_CODEPAGES:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    return data.decode("latin-1"), "latin-1(fallback)"


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    encoding: str = "utf-8"

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class DiskProbeResult:
    """Mesure d'espace disque, avec le chemin REELLEMENT mesure.

    Quatre situations doivent rester distinguables, car elles n'appellent pas
    la meme action :

    - le chemin demande existe et a ete mesure ;
    - il n'existe pas encore, mais un ancetre existant a ete mesure (meme
      systeme de fichiers : la mesure est valide pour le futur chemin) ;
    - aucune mesure n'est possible ;
    - une erreur systeme empeche la mesure (permissions, par exemple).

    Les confondre reviendrait a signaler un probleme de disque la ou il n'y a
    qu'un repertoire pas encore cree.
    """

    requested_path: str
    measured_path: str | None
    total_gb: float | None
    free_gb: float | None
    requested_exists: bool
    error: str | None = None

    @property
    def measured(self) -> bool:
        return self.free_gb is not None

    @property
    def fell_back_to_ancestor(self) -> bool:
        return self.measured and self.measured_path != self.requested_path


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: str

    def json(self) -> Any:
        return json.loads(self.body)


class Probe(Protocol):
    """Inspection du système, en lecture seule, avec timeout obligatoire."""

    def which(self, program: str) -> str | None: ...
    def run(self, argv: list[str], *, timeout_s: float) -> CommandResult: ...
    def read_text(self, path: str, *, max_bytes: int = 65536) -> str | None: ...
    def exists(self, path: str) -> bool: ...
    def disk_probe(self, path: str) -> DiskProbeResult: ...
    def disk_free_gb(self, path: str) -> float | None: ...
    def tcp_open(self, host: str, port: int, *, timeout_s: float) -> bool: ...
    def http_get(self, url: str, *, timeout_s: float) -> HttpResult | None: ...
    def env(self, key: str) -> str | None: ...
    def platform_info(self) -> dict[str, str]: ...


class SystemProbe:
    """Implémentation réelle. Aucune écriture, aucune élévation de privilège."""

    def which(self, program: str) -> str | None:
        return shutil.which(program)

    def run(self, argv: list[str], *, timeout_s: float) -> CommandResult:
        """Execute une commande en capturant des OCTETS, jamais du texte.

        `text=True` delegue le decodage a subprocess, qui utilise l'encodage
        local (UTF-8 sous WSL) en mode strict et leve UnicodeDecodeError sur
        toute sortie en page de code Windows. On capture donc des octets et on
        decode explicitement, avec une strategie documentee et un encodage
        rapporte.
        """
        try:
            proc = subprocess.run(  # noqa: S603 - argv est toujours une liste litterale
                argv,
                capture_output=True,
                text=False,
                timeout=timeout_s,
                check=False,
            )
        except FileNotFoundError:
            return CommandResult(tuple(argv), 127, "", f"introuvable : {argv[0]}")
        except subprocess.TimeoutExpired:
            return CommandResult(tuple(argv), -1, "", "timeout", timed_out=True)
        except OSError as exc:  # pragma: no cover - depend de l'hote
            return CommandResult(tuple(argv), -1, "", str(exc))

        stdout, encoding = decode_process_output(proc.stdout or b"")
        stderr, _ = decode_process_output(proc.stderr or b"")
        return CommandResult(tuple(argv), proc.returncode, stdout, stderr, encoding=encoding)

    def read_text(self, path: str, *, max_bytes: int = 65536) -> str | None:
        try:
            with open(path, "rb") as handle:
                return handle.read(max_bytes).decode("utf-8", errors="replace")
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def disk_probe(self, path: str) -> DiskProbeResult:
        """Mesure l'espace disque, en remontant vers un ancetre existant.

        Le repertoire de donnees d'APEX est cree a l'usage, pas a
        l'installation : sur une machine neuve il n'existe pas encore. Mesurer
        son systeme de fichiers via le premier ancetre existant est correct —
        c'est le meme point de montage — et ne cree rien.

        Aucun repertoire n'est cree : ce serait une ecriture, et la sonde est
        en lecture seule.
        """
        requested = Path(path)
        exists = requested.exists()

        candidate: Path | None = requested if exists else None
        if candidate is None:
            for parent in requested.parents:
                if parent.exists():
                    candidate = parent
                    break

        if candidate is None:
            return DiskProbeResult(
                requested_path=path, measured_path=None, total_gb=None, free_gb=None,
                requested_exists=exists, error="aucun ancetre existant",
            )

        try:
            usage = shutil.disk_usage(str(candidate))
        except OSError as exc:
            # Erreur systeme reelle (permissions, peripherique) : distincte de
            # « chemin absent ». Elle doit remonter telle quelle.
            return DiskProbeResult(
                requested_path=path, measured_path=str(candidate), total_gb=None, free_gb=None,
                requested_exists=exists, error=f"{exc.__class__.__name__}: {exc}",
            )

        return DiskProbeResult(
            requested_path=path,
            measured_path=str(candidate),
            total_gb=usage.total / (1024**3),
            free_gb=usage.free / (1024**3),
            requested_exists=exists,
        )

    def disk_free_gb(self, path: str) -> float | None:
        """Conserve pour compatibilite ; delegue a disk_probe (source unique)."""
        return self.disk_probe(path).free_gb

    def tcp_open(self, host: str, port: int, *, timeout_s: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                return True
        except OSError:
            return False

    def http_get(self, url: str, *, timeout_s: float) -> HttpResult | None:
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
                body = response.read(1_000_000).decode("utf-8", errors="replace")
                return HttpResult(status=response.status, body=body)
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def env(self, key: str) -> str | None:
        return os.environ.get(key)

    def platform_info(self) -> dict[str, str]:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        }


@dataclass
class FakeProbe:
    """Probe déterministe pour les tests. Tout est explicite ; rien n'est deviné."""

    programs: dict[str, str] = field(default_factory=dict)
    commands: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    free_gb: dict[str, float] = field(default_factory=dict)
    disk_total_gb: dict[str, float] = field(default_factory=dict)
    disk_errors: dict[str, str] = field(default_factory=dict)
    open_ports: set[tuple[str, int]] = field(default_factory=set)
    http: dict[str, HttpResult] = field(default_factory=dict)
    environ: dict[str, str] = field(default_factory=dict)
    platform: dict[str, str] = field(
        default_factory=lambda: {
            "system": "Linux",
            "release": "6.6.0-microsoft-standard-WSL2",
            "machine": "x86_64",
            "python": "3.12.3",
        }
    )
    calls: list[tuple[str, str]] = field(default_factory=list)

    def which(self, program: str) -> str | None:
        self.calls.append(("which", program))
        return self.programs.get(program)

    def run(self, argv: list[str], *, timeout_s: float) -> CommandResult:
        key = tuple(argv)
        self.calls.append(("run", " ".join(argv)))
        if key in self.commands:
            return self.commands[key]
        return CommandResult(key, 127, "", f"introuvable : {argv[0]}")

    def read_text(self, path: str, *, max_bytes: int = 65536) -> str | None:
        self.calls.append(("read", path))
        content = self.files.get(path)
        return content[:max_bytes] if content is not None else None

    def exists(self, path: str) -> bool:
        return path in self.paths or path in self.files

    def disk_probe(self, path: str) -> DiskProbeResult:
        """Rejoue la meme remontee vers l'ancetre que la sonde reelle."""
        exists = self.exists(path)

        if path in self.disk_errors:
            return DiskProbeResult(path, path, None, None, exists, self.disk_errors[path])

        candidate = path if path in self.free_gb else None
        if candidate is None:
            for parent in PurePosixPath(path).parents:
                key = str(parent)
                if key in self.disk_errors:
                    return DiskProbeResult(path, key, None, None, exists, self.disk_errors[key])
                if key in self.free_gb:
                    candidate = key
                    break

        if candidate is None:
            return DiskProbeResult(path, None, None, None, exists, "aucun ancetre existant")

        free = self.free_gb[candidate]
        return DiskProbeResult(path, candidate, self.disk_total_gb.get(candidate), free, exists)

    def disk_free_gb(self, path: str) -> float | None:
        return self.disk_probe(path).free_gb

    def tcp_open(self, host: str, port: int, *, timeout_s: float) -> bool:
        self.calls.append(("tcp", f"{host}:{port}"))
        return (host, port) in self.open_ports

    def http_get(self, url: str, *, timeout_s: float) -> HttpResult | None:
        self.calls.append(("http", url))
        return self.http.get(url)

    def env(self, key: str) -> str | None:
        return self.environ.get(key)

    def platform_info(self) -> dict[str, str]:
        return dict(self.platform)
