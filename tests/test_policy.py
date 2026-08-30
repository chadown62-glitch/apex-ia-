"""Tests du PolicyEngine — autorisation avant tout effet de bord.

Le cas le plus important n'est pas "l'allow-list fonctionne" (trivial) mais
"les interdits absolus gagnent même si l'allow-list dit le contraire" :
c'est la garantie qui protège le Core d'une allow-list mal construite plus
tard, humaine ou générée.
"""

from __future__ import annotations

import unittest

from apex.errors import PolicyDenied
from apex.policy.engine import DEFAULT_ALLOWLIST, PolicyEngine, _ABSOLUTE_DENY
from apex.policy.model import Action, PolicyDecision, Subject


class TestAbsoluteDeny(unittest.TestCase):
    def test_absolute_deny_wins_even_if_a_custom_allowlist_grants_it(self) -> None:
        """Une allow-list mal construite ne peut pas rouvrir un interdit absolu."""
        reckless_allowlist = {Subject.CORE: frozenset(_ABSOLUTE_DENY)}
        engine = PolicyEngine(allowlist=reckless_allowlist)
        for action in _ABSOLUTE_DENY:
            with self.subTest(action=action):
                decision = engine.check(Subject.CORE, action)
                self.assertFalse(decision.allowed)

    def test_every_subject_is_denied_every_absolute_action(self) -> None:
        engine = PolicyEngine()
        for subject in Subject:
            for action in _ABSOLUTE_DENY:
                with self.subTest(subject=subject, action=action):
                    self.assertFalse(engine.check(subject, action).allowed)

    def test_absolute_deny_covers_the_limits_named_in_cognitive_architecture(self) -> None:
        """§2.11 de docs/COGNITIVE_ARCHITECTURE.md : Core, Gate, registre, ADR,
        audit_log, origin, main, auto-élévation, désactivation du Gate."""
        expected = {
            Action.MODIFY_CORE, Action.MODIFY_GATE, Action.MODIFY_REGISTRY,
            Action.MODIFY_ADR, Action.MODIFY_AUDIT_LOG, Action.GIT_PUSH_ORIGIN,
            Action.MERGE_TO_MAIN, Action.ESCALATE_OWN_PRIVILEGE, Action.DISABLE_GATE,
        }
        self.assertEqual(_ABSOLUTE_DENY, expected)


class TestDefaultAllowlist(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = PolicyEngine()

    def test_research_clone_can_reach_network(self) -> None:
        self.assertTrue(self.engine.check(Subject.RESEARCH, Action.NETWORK_EGRESS).allowed)

    def test_only_research_can_reach_network(self) -> None:
        for subject in Subject:
            if subject is Subject.RESEARCH:
                continue
            with self.subTest(subject=subject):
                self.assertFalse(self.engine.check(subject, Action.NETWORK_EGRESS).allowed)

    def test_developer_can_write_its_worktree(self) -> None:
        self.assertTrue(self.engine.check(Subject.DEVELOPER, Action.WRITE_OWN_WORKTREE).allowed)

    def test_developer_cannot_propose_integration(self) -> None:
        """Seul l'Integrator soumet — le Developer implémente, il ne propose pas
        lui-même (docs/COGNITIVE_ARCHITECTURE.md §2.7)."""
        self.assertFalse(self.engine.check(Subject.DEVELOPER, Action.PROPOSE_INTEGRATION).allowed)

    def test_integrator_can_propose_but_cannot_write_a_worktree(self) -> None:
        self.assertTrue(self.engine.check(Subject.INTEGRATOR, Action.PROPOSE_INTEGRATION).allowed)
        self.assertFalse(self.engine.check(Subject.INTEGRATOR, Action.WRITE_OWN_WORKTREE).allowed)

    def test_only_security_and_audit_capable_subjects_can_write_audit_log(self) -> None:
        capable = {s for s in Subject if Action.WRITE_AUDIT_LOG in DEFAULT_ALLOWLIST.get(s, frozenset())}
        self.assertEqual(capable, {Subject.CORE, Subject.HUMAN, Subject.SECURITY})

    def test_only_core_and_human_can_spawn_or_terminate_clones(self) -> None:
        for subject in (Subject.CORE, Subject.HUMAN):
            with self.subTest(subject=subject):
                self.assertTrue(self.engine.check(subject, Action.SPAWN_CLONE).allowed)
                self.assertTrue(self.engine.check(subject, Action.TERMINATE_CLONE).allowed)
        for subject in Subject:
            if subject in (Subject.CORE, Subject.HUMAN):
                continue
            with self.subTest(subject=subject):
                self.assertFalse(self.engine.check(subject, Action.SPAWN_CLONE).allowed)

    def test_only_core_can_execute_in_sandbox(self) -> None:
        """Étape 7 : ni HUMAN ni aucun clone ne peut déclencher lui-même
        l'exécution en sandbox — seul CORE, l'orchestrateur système."""
        self.assertTrue(self.engine.check(Subject.CORE, Action.EXECUTE_IN_SANDBOX).allowed)
        for subject in Subject:
            if subject is Subject.CORE:
                continue
            with self.subTest(subject=subject):
                self.assertFalse(self.engine.check(subject, Action.EXECUTE_IN_SANDBOX).allowed)

    def test_promote_memory_to_shared_is_granted_to_no_one_by_default(self) -> None:
        """Promouvoir un souvenir de clone vers la mémoire partagée reste un
        acte explicite et audité, jamais un droit par défaut (§2.5)."""
        for subject in Subject:
            with self.subTest(subject=subject):
                self.assertFalse(self.engine.check(subject, Action.PROMOTE_MEMORY_TO_SHARED).allowed)


class TestDenyByDefault(unittest.TestCase):
    def test_subject_absent_from_allowlist_is_denied_not_ignored(self) -> None:
        engine = PolicyEngine(allowlist={})
        decision = engine.check(Subject.RESEARCH, Action.NETWORK_EGRESS)
        self.assertFalse(decision.allowed)
        self.assertIn("non autorisé", decision.reason)

    def test_check_never_raises(self) -> None:
        engine = PolicyEngine()
        try:
            engine.check(Subject.DEVELOPER, Action.MODIFY_CORE)
        except Exception as exc:  # pragma: no cover - échec du test si levée
            self.fail(f"check() a levé {exc!r} — elle ne doit jamais lever")

    def test_authorize_raises_policy_denied_with_reason(self) -> None:
        engine = PolicyEngine()
        with self.assertRaises(PolicyDenied) as ctx:
            engine.authorize(Subject.DEVELOPER, Action.MERGE_TO_MAIN)
        self.assertEqual(ctx.exception.subject, "developer")
        self.assertEqual(ctx.exception.action, "merge_to_main")
        self.assertTrue(ctx.exception.reason)

    def test_authorize_returns_none_when_allowed(self) -> None:
        engine = PolicyEngine()
        self.assertIsNone(engine.authorize(Subject.RESEARCH, Action.NETWORK_EGRESS))


class TestCustomAllowlist(unittest.TestCase):
    def test_custom_allowlist_overrides_default_for_ordinary_actions(self) -> None:
        custom = {Subject.LEARNING: frozenset({Action.NETWORK_EGRESS})}
        engine = PolicyEngine(allowlist=custom)
        self.assertTrue(engine.check(Subject.LEARNING, Action.NETWORK_EGRESS).allowed)
        self.assertFalse(engine.check(Subject.RESEARCH, Action.NETWORK_EGRESS).allowed)

    def test_custom_allowlist_still_bound_by_absolute_deny(self) -> None:
        custom = {Subject.LEARNING: frozenset({Action.MODIFY_CORE})}
        engine = PolicyEngine(allowlist=custom)
        self.assertFalse(engine.check(Subject.LEARNING, Action.MODIFY_CORE).allowed)


class TestPolicyDecision(unittest.TestCase):
    def test_decision_is_a_frozen_dataclass(self) -> None:
        decision = PolicyEngine().check(Subject.RESEARCH, Action.NETWORK_EGRESS)
        self.assertIsInstance(decision, PolicyDecision)
        with self.assertRaises(Exception):
            decision.allowed = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
