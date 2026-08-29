"""Registre de métriques en processus, stdlib uniquement.

Principe 13 : tout composant important possède des métriques dès sa création.
Ce registre est délibérément minimal — il fournit le contrat (Counter, Gauge,
Histogram, timer) et un rendu Prometheus. Le passage à un exporteur réel ou à
OpenTelemetry (phase 20) ne changera pas les points d'appel.

Choix technique : histogrammes à buckets fixes plutôt que quantiles exacts.
Les buckets sont agrégeables entre workers, ce qui sera nécessaire dès qu'un
second ComputePool existera ; des quantiles calculés localement ne le sont pas.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

Labels = tuple[tuple[str, str], ...]

DEFAULT_BUCKETS = (0.005, 0.025, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 900.0)


def _labels(mapping: dict[str, str] | None) -> Labels:
    if not mapping:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in mapping.items()))


@dataclass
class _Series:
    kind: str
    help_text: str
    values: dict[Labels, float] = field(default_factory=dict)
    buckets: dict[Labels, list[int]] = field(default_factory=dict)
    counts: dict[Labels, int] = field(default_factory=dict)
    sums: dict[Labels, float] = field(default_factory=dict)


class MetricsRegistry:
    """Registre thread-safe. Une instance par processus."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict[str, _Series] = {}

    # -- déclaration -------------------------------------------------------

    def _ensure(self, name: str, kind: str, help_text: str) -> _Series:
        series = self._series.get(name)
        if series is None:
            series = _Series(kind=kind, help_text=help_text)
            self._series[name] = series
        elif series.kind != kind:
            raise ValueError(f"métrique « {name} » déjà déclarée comme {series.kind}")
        return series

    # -- écriture ----------------------------------------------------------

    def counter(self, name: str, value: float = 1.0, *, help_text: str = "", **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            series = self._ensure(name, "counter", help_text)
            series.values[key] = series.values.get(key, 0.0) + value

    def gauge(self, name: str, value: float, *, help_text: str = "", **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            series = self._ensure(name, "gauge", help_text)
            series.values[key] = value

    def observe(self, name: str, value: float, *, help_text: str = "", **labels: str) -> None:
        key = _labels(labels)
        with self._lock:
            series = self._ensure(name, "histogram", help_text)
            buckets = series.buckets.setdefault(key, [0] * (len(DEFAULT_BUCKETS) + 1))
            index = len(DEFAULT_BUCKETS)
            for i, bound in enumerate(DEFAULT_BUCKETS):
                if value <= bound:
                    index = i
                    break
            buckets[index] += 1
            series.counts[key] = series.counts.get(key, 0) + 1
            series.sums[key] = series.sums.get(key, 0.0) + value

    @contextmanager
    def timer(self, name: str, *, help_text: str = "", **labels: str) -> Iterator[None]:
        """Mesure une durée en secondes, y compris en cas d'exception."""
        start = time.perf_counter()
        outcome = "ok"
        try:
            yield
        except BaseException:
            outcome = "error"
            raise
        finally:
            self.observe(
                name, time.perf_counter() - start, help_text=help_text, outcome=outcome, **labels
            )

    # -- lecture -----------------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            out: dict[str, dict[str, object]] = {}
            for name, series in self._series.items():
                entry: dict[str, object] = {"kind": series.kind}
                if series.kind == "histogram":
                    entry["count"] = dict(series.counts)
                    entry["sum"] = dict(series.sums)
                    entry["buckets"] = {k: list(v) for k, v in series.buckets.items()}
                else:
                    entry["values"] = dict(series.values)
                out[name] = entry
            return out

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, series in sorted(self._series.items()):
                if series.help_text:
                    lines.append(f"# HELP {name} {series.help_text}")
                lines.append(f"# TYPE {name} {series.kind}")
                if series.kind == "histogram":
                    for key, buckets in sorted(series.buckets.items()):
                        cumulative = 0
                        for i, bound in enumerate(DEFAULT_BUCKETS):
                            cumulative += buckets[i]
                            lines.append(
                                f"{name}_bucket{_fmt(key, le=str(bound))} {cumulative}"
                            )
                        cumulative += buckets[-1]
                        lines.append(f"{name}_bucket{_fmt(key, le='+Inf')} {cumulative}")
                        lines.append(f"{name}_sum{_fmt(key)} {series.sums.get(key, 0.0)}")
                        lines.append(f"{name}_count{_fmt(key)} {series.counts.get(key, 0)}")
                else:
                    for key, value in sorted(series.values.items()):
                        lines.append(f"{name}{_fmt(key)} {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._series.clear()


def _fmt(labels: Labels, **extra: str) -> str:
    items = list(labels) + sorted(extra.items())
    if not items:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in items)
    return "{" + inner + "}"


REGISTRY = MetricsRegistry()
