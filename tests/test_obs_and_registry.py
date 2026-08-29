"""Tests d'observabilité et d'intégrité du registre d'abstractions.

Le test le plus important de ce fichier est
`TestRegistryHonesty.test_registry_cannot_overstate_reality` : il empêche le
registre de déclarer une abstraction disponible alors qu'aucune implémentation
n'existe. C'est la garantie mécanique derrière le principe 15 — le rapport de
fin de phase ne peut pas embellir la situation, parce que la suite de tests
échouerait.
"""

from __future__ import annotations

import io
import json
import logging
import unittest

from apex.errors import IsolationUnavailable, NotImplementedYet, TimeoutExceeded
from apex.interfaces.registry import (
    ABSTRACTIONS,
    ImplStatus,
    available_at_phase,
    by_name,
    module_importable,
    summary,
)
from apex.obs.health import ComponentHealth, Status, aggregate
from apex.obs.logging import JsonFormatter, configure, get_logger, redact
from apex.obs.metrics import MetricsRegistry


class TestSecretRedaction(unittest.TestCase):
    """§72 : aucun secret ne doit pouvoir atteindre un journal."""

    def test_secret_keys_are_masked(self) -> None:
        out = redact({"api_key": "abc123", "password": "hunter2", "user": "rafael"})
        self.assertNotIn("abc123", json.dumps(out))
        self.assertNotIn("hunter2", json.dumps(out))
        self.assertEqual(out["user"], "rafael")

    def test_nested_structures_are_masked(self) -> None:
        out = redact({"cfg": [{"token": "t-secret"}, {"ok": 1}]})
        self.assertNotIn("t-secret", json.dumps(out))

    def test_secrets_inside_free_text_are_masked(self) -> None:
        text = redact("connect postgres://apex:supersecret@localhost:5432/apex")
        self.assertNotIn("supersecret", text)
        self.assertIn("postgres://apex:", text)

    def test_bearer_and_provider_keys_are_masked(self) -> None:
        self.assertNotIn("sk-abcdefghijklmnopqrst", redact("key sk-abcdefghijklmnopqrst here"))
        self.assertNotIn("abcdefghijklmnopqrst", redact("Authorization: Bearer abcdefghijklmnopqrst"))

    def test_redaction_happens_in_the_formatter(self) -> None:
        """La rédaction ne dépend pas de la discipline de l'appelant."""
        stream = io.StringIO()
        configure(level="INFO", stream=stream)
        get_logger("t").info("connexion", password="hunter2")
        payload = json.loads(stream.getvalue().strip())
        self.assertNotIn("hunter2", json.dumps(payload))
        self.assertEqual(payload["ctx"]["password"], "***redacted***")


class TestLogging(unittest.TestCase):
    def test_bound_context_is_carried(self) -> None:
        stream = io.StringIO()
        configure(level="INFO", stream=stream)
        get_logger("t").bind(task_id="t-1").info("étape", step=2)
        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["ctx"]["task_id"], "t-1")
        self.assertEqual(payload["ctx"]["step"], 2)

    def test_output_is_one_json_object_per_line(self) -> None:
        record = logging.LogRecord("apex.t", logging.INFO, __file__, 1, "msg", None, None)
        parsed = json.loads(JsonFormatter().format(record))
        self.assertEqual(parsed["msg"], "msg")
        self.assertIn("ts", parsed)


class TestMetrics(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = MetricsRegistry()

    def test_counter_and_labels(self) -> None:
        self.registry.counter("runs_total", tool="pytest")
        self.registry.counter("runs_total", 2, tool="pytest")
        self.registry.counter("runs_total", tool="cargo")
        snapshot = self.registry.snapshot()["runs_total"]["values"]
        self.assertEqual(snapshot[(("tool", "pytest"),)], 3.0)
        self.assertEqual(snapshot[(("tool", "cargo"),)], 1.0)

    def test_timer_records_even_on_exception(self) -> None:
        with self.assertRaises(ValueError):
            with self.registry.timer("op_seconds"):
                raise ValueError("échec")
        entry = self.registry.snapshot()["op_seconds"]
        self.assertEqual(sum(entry["count"].values()), 1)
        labels = next(iter(entry["count"]))
        self.assertIn(("outcome", "error"), labels)

    def test_type_conflict_is_rejected(self) -> None:
        self.registry.counter("x")
        with self.assertRaises(ValueError):
            self.registry.gauge("x", 1.0)

    def test_prometheus_rendering(self) -> None:
        self.registry.gauge("vram_gb", 24.0, pool="local-gpu")
        self.registry.observe("dur_seconds", 0.3)
        text = self.registry.render_prometheus()
        self.assertIn('vram_gb{pool="local-gpu"} 24.0', text)
        self.assertIn("dur_seconds_bucket", text)
        self.assertIn('le="+Inf"', text)


class TestHealth(unittest.TestCase):
    def test_worst_status_wins(self) -> None:
        system = aggregate([
            ComponentHealth("db", Status.OK),
            ComponentHealth("ollama", Status.DEGRADED, fallback_active=True),
        ])
        self.assertIs(system.status, Status.DEGRADED)
        self.assertEqual(len(system.degraded), 1)

    def test_degraded_is_not_failure(self) -> None:
        """Fonctionner sans services externes est un état sain-dégradé."""
        system = aggregate([ComponentHealth("external", Status.DEGRADED, fallback_active=True)])
        self.assertIsNot(system.status, Status.FAILED)
        self.assertEqual(system.failed, ())


class TestErrors(unittest.TestCase):
    def test_not_implemented_yet_names_phase_and_fallback(self) -> None:
        exc = NotImplementedYet("GenomeStore", phase=17, fallback="génome unique figé")
        self.assertEqual(exc.phase, 17)
        self.assertIn("phase 17", str(exc))
        self.assertIn("génome unique figé", str(exc))

    def test_isolation_unavailable_states_the_gap(self) -> None:
        exc = IsolationUnavailable("red_team", required="gvisor", available="docker_hardened")
        self.assertIn("désactivée", str(exc))

    def test_timeout_carries_budget(self) -> None:
        self.assertIn("12.0s", str(TimeoutExceeded("eval", 12.0)))


class TestRegistryHonesty(unittest.TestCase):
    def test_registry_cannot_overstate_reality(self) -> None:
        """Une abstraction disponible doit avoir un module réellement importable."""
        liars = [
            a.name
            for a in ABSTRACTIONS
            if a.status in (ImplStatus.IMPLEMENTED, ImplStatus.STUB) and not module_importable(a)
        ]
        self.assertEqual(liars, [], f"registre optimiste sur : {liars}")

    def test_planned_abstractions_declare_no_module(self) -> None:
        wrong = [a.name for a in ABSTRACTIONS if a.status is ImplStatus.PLANNED and a.module]
        self.assertEqual(wrong, [])

    def test_every_abstraction_declares_a_fallback(self) -> None:
        """Principe 12 : interface -> implémentation -> test -> repli."""
        missing = [a.name for a in ABSTRACTIONS if not a.fallback]
        self.assertEqual(missing, [])

    def test_definition_never_after_implementation(self) -> None:
        wrong = [a.name for a in ABSTRACTIONS if a.phase_defined > a.phase_implemented]
        self.assertEqual(wrong, [])

    def test_phase_one_promises_nothing_it_cannot_keep(self) -> None:
        for item in available_at_phase(1):
            self.assertIs(item.status, ImplStatus.IMPLEMENTED, f"{item.name} promise en phase 1")

    def test_summary_covers_all_abstractions(self) -> None:
        self.assertEqual(sum(summary().values()), len(ABSTRACTIONS))
        self.assertEqual(by_name("ComputePool").phase_defined, 1)


if __name__ == "__main__":
    unittest.main()
