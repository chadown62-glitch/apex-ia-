"""Tests de `apex.desktop.allowlist` — la frontière de sécurité RÉELLE du
contrôle clavier/souris. Aucune dépendance système ici (pas de vrai écran,
pas de vrai backend) : ce module doit être exhaustivement prouvable en pur
Python, puisque c'est la seule chose qui empêche une action non désirée de
partir — un backend bogué ne peut agir que via son verdict."""

from __future__ import annotations

import unittest

from apex.desktop.allowlist import DEFAULT_DENYLIST_PATTERNS, build_policy, is_action_permitted
from apex.desktop.model import (
    AllowedTarget,
    DesktopAction,
    DesktopActionKind,
    PermissionDecision,
    WindowInfo,
)


def click(x: int = 10, y: int = 10) -> DesktopAction:
    return DesktopAction(kind=DesktopActionKind.CLICK, x=x, y=y)


class TestDefaultDenyByDefault(unittest.TestCase):
    """Le cas le plus important : SANS aucune entrée d'allowlist, tout est
    refusé — jamais une politique vide interprétée comme « tout permis »."""

    def test_a_empty_policy_denies_every_non_readonly_action(self) -> None:
        policy = build_policy()
        window = WindowInfo(title="Notepad", process_name="notepad.exe")
        decision = is_action_permitted(click(), window, policy)
        self.assertIs(decision, PermissionDecision.DENIED_NOT_ALLOWLISTED)

    def test_b_no_active_window_is_denied_never_allowed_by_default(self) -> None:
        policy = build_policy((AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),))
        decision = is_action_permitted(click(), None, policy)
        self.assertIs(decision, PermissionDecision.DENIED_NO_ACTIVE_WINDOW)


class TestReadOnlyActionsAlwaysAllowed(unittest.TestCase):
    def test_a_screenshot_allowed_even_with_no_window(self) -> None:
        policy = build_policy()
        action = DesktopAction(kind=DesktopActionKind.SCREENSHOT)
        self.assertIs(is_action_permitted(action, None, policy), PermissionDecision.ALLOWED)

    def test_b_wait_allowed_even_on_a_denylisted_window(self) -> None:
        policy = build_policy()
        window = WindowInfo(title="Enter your password", process_name="chrome.exe")
        action = DesktopAction(kind=DesktopActionKind.WAIT, duration_s=1.0)
        self.assertIs(is_action_permitted(action, window, policy), PermissionDecision.ALLOWED)


class TestAllowlistMatching(unittest.TestCase):
    def test_a_matching_title_and_allowed_kind_is_allowed(self) -> None:
        policy = build_policy((AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Untitled - Notepad", process_name="notepad.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.ALLOWED)

    def test_b_matching_title_but_disallowed_kind_is_denied(self) -> None:
        policy = build_policy((AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Untitled - Notepad", process_name="notepad.exe")
        action = DesktopAction(kind=DesktopActionKind.TYPE_TEXT, text="hello")
        self.assertIs(
            is_action_permitted(action, window, policy), PermissionDecision.DENIED_ACTION_KIND_NOT_PERMITTED
        )

    def test_c_non_matching_title_is_denied(self) -> None:
        policy = build_policy((AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Calculator", process_name="calc.exe")
        decision = is_action_permitted(click(), window, policy)
        self.assertIs(decision, PermissionDecision.DENIED_NOT_ALLOWLISTED)

    def test_d_title_match_is_case_insensitive(self) -> None:
        policy = build_policy((AllowedTarget("notepad", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="UNTITLED - NOTEPAD", process_name="notepad.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.ALLOWED)

    def test_e_title_match_is_substring_not_exact(self) -> None:
        policy = build_policy((AllowedTarget("Calculator", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Calculator - Standard", process_name="calc.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.ALLOWED)

    def test_f_process_name_when_specified_must_also_match(self) -> None:
        policy = build_policy(
            (AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK}), process_name="notepad.exe"),)
        )
        window = WindowInfo(title="evil-Notepad-lookalike", process_name="malware.exe")
        decision = is_action_permitted(click(), window, policy)
        self.assertIs(decision, PermissionDecision.DENIED_NOT_ALLOWLISTED)

    def test_g_process_name_match_when_both_correct_is_allowed(self) -> None:
        policy = build_policy(
            (AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK}), process_name="notepad.exe"),)
        )
        window = WindowInfo(title="Untitled - Notepad", process_name="notepad.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.ALLOWED)

    def test_h_multiple_targets_any_matching_one_permitting_the_kind_is_enough(self) -> None:
        policy = build_policy((
            AllowedTarget("Notepad", frozenset({DesktopActionKind.CLICK})),
            AllowedTarget("Notepad", frozenset({DesktopActionKind.TYPE_TEXT})),
        ))
        window = WindowInfo(title="Untitled - Notepad", process_name="notepad.exe")
        action = DesktopAction(kind=DesktopActionKind.TYPE_TEXT, text="hi")
        self.assertIs(is_action_permitted(action, window, policy), PermissionDecision.ALLOWED)


class TestDenylistAlwaysWins(unittest.TestCase):
    """La garantie la plus critique du module : le denylist prime TOUJOURS,
    même quand une entrée d'allowlist matcherait aussi la même fenêtre —
    jamais contournable depuis la configuration utilisateur."""

    def test_a_denylisted_title_beats_a_matching_allowlist_entry(self) -> None:
        policy = build_policy((AllowedTarget("Chrome", frozenset({DesktopActionKind.CLICK})),))
        window = WindowInfo(title="Sign in - Google Chrome - password required", process_name="chrome.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.DENIED_DENYLISTED)

    def test_b_every_default_denylist_pattern_actually_denies(self) -> None:
        """Non-régression exhaustive : CHAQUE motif déclaré dans
        `DEFAULT_DENYLIST_PATTERNS` doit réellement produire un refus, même
        avec une entrée d'allowlist qui matcherait sinon tout — un motif
        ajouté à la liste sans jamais être exercé ici serait une fausse
        garantie de sécurité."""
        policy = build_policy((AllowedTarget("", frozenset(DesktopActionKind)),))  # matcherait TOUT titre
        for pattern in DEFAULT_DENYLIST_PATTERNS:
            with self.subTest(pattern=pattern):
                window = WindowInfo(title=f"Some App - {pattern}", process_name="app.exe")
                decision = is_action_permitted(click(), window, policy)
                self.assertIs(decision, PermissionDecision.DENIED_DENYLISTED)

    def test_c_denylist_check_is_case_insensitive(self) -> None:
        policy = build_policy((AllowedTarget("", frozenset(DesktopActionKind)),))
        window = WindowInfo(title="ENTER YOUR PASSWORD", process_name="app.exe")
        self.assertIs(is_action_permitted(click(), window, policy), PermissionDecision.DENIED_DENYLISTED)

    def test_d_extra_denylist_patterns_are_additive_never_replacing(self) -> None:
        policy = build_policy(
            (AllowedTarget("", frozenset(DesktopActionKind)),), extra_denylist_patterns=("banque",)
        )
        # le motif custom fonctionne
        w1 = WindowInfo(title="Mon Espace Banque en ligne", process_name="chrome.exe")
        self.assertIs(is_action_permitted(click(), w1, policy), PermissionDecision.DENIED_DENYLISTED)
        # ET les motifs par défaut restent actifs
        w2 = WindowInfo(title="Enter password", process_name="chrome.exe")
        self.assertIs(is_action_permitted(click(), w2, policy), PermissionDecision.DENIED_DENYLISTED)

    def test_e_no_way_to_construct_a_policy_bypassing_the_default_denylist(self) -> None:
        """`build_policy` est le SEUL point de construction légitime — sans
        arguments, le denylist par défaut est quand même présent."""
        policy = build_policy()
        self.assertEqual(set(DEFAULT_DENYLIST_PATTERNS), set(policy.denylist_title_patterns))


if __name__ == "__main__":
    unittest.main()
