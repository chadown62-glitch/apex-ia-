"""Capture BORNÉE de la sortie d'un processus sandboxé (06/10/2026).

Avant : `Popen.communicate()` accumulait TOUTE la sortie en mémoire de l'hôte puis tronquait — une boucle
d'impression infinie pendant le délai pouvait épuiser la mémoire de l'hôte, et la troncature ne gardait que le
DÉBUT (le résumé d'un lanceur de tests, toujours à la fin, était perdu). Ici, chaque flux est lu en continu
(jamais bloqué, donc le processus n'est jamais suspendu sur un tube plein) ; seuls les `limit` premiers et les
`limit` derniers octets sont conservés, le reste est compté puis jeté.
"""

from __future__ import annotations

import subprocess
import threading
from collections import deque

_CHUNK = 65_536


class _Stream:
    def __init__(self, pipe, limit: int) -> None:
        self.pipe = pipe
        self.limit = limit
        self.head = bytearray()
        self.tail: deque[bytes] = deque()
        self.tail_size = 0
        self.total = 0
        self.thread = threading.Thread(target=self._drain, daemon=True)

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.pipe.read1(_CHUNK) if hasattr(self.pipe, "read1") else self.pipe.read(_CHUNK)
                if not chunk:
                    return
                self.total += len(chunk)
                room = self.limit - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail.append(chunk)
                    self.tail_size += len(chunk)
                    while self.tail_size - len(self.tail[0]) >= self.limit:
                        self.tail_size -= len(self.tail.popleft())
        except (OSError, ValueError):
            return
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass

    def result(self) -> tuple[bytes, bool]:
        tail = b"".join(self.tail)[-self.limit:] if self.tail else b""
        kept = len(self.head) + len(tail)
        if self.total <= kept:
            return bytes(self.head) + tail, False
        omitted = self.total - kept
        marker = f"\n[... {omitted} octets omis par APEX (sortie bornée) ...]\n".encode()
        return bytes(self.head) + marker + tail, True


class BoundedCapture:
    """`start()` -> `wait(timeout)` (False si délai dépassé : l'appelant tue le processus) -> `finish()`."""

    def __init__(self, proc: subprocess.Popen, *, limit: int, input_bytes: bytes | None = None) -> None:
        self.proc = proc
        self.out = _Stream(proc.stdout, limit)
        self.err = _Stream(proc.stderr, limit)
        self.input_bytes = input_bytes
        self.writer: threading.Thread | None = None

    def start(self) -> BoundedCapture:
        self.out.thread.start()
        self.err.thread.start()
        if self.proc.stdin is not None:
            self.writer = threading.Thread(target=self._feed, daemon=True)
            self.writer.start()
        return self

    def _feed(self) -> None:
        try:
            if self.input_bytes:
                self.proc.stdin.write(self.input_bytes)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def wait(self, timeout_s: float) -> bool:
        try:
            self.proc.wait(timeout=timeout_s)
            return True
        except subprocess.TimeoutExpired:
            return False

    def finish(self, *, join_timeout_s: float = 30.0) -> tuple[bytes, bytes, bool, bool]:
        """Attend la fin du processus et des lecteurs ; (stdout, stderr, stdout_tronqué, stderr_tronqué)."""
        self.proc.wait()
        for thread in (self.out.thread, self.err.thread, self.writer):
            if thread is not None:
                thread.join(timeout=join_timeout_s)
        stdout, out_cut = self.out.result()
        stderr, err_cut = self.err.result()
        return stdout, stderr, out_cut, err_cut
