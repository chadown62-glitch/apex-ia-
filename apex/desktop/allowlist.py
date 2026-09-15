"""La frontière de sécurité RÉELLE de `apex.desktop` — pure, sans aucune
dépendance au système (aucun import de backend d'automatisation ici), donc
exhaustivement testable et, par construction, la seule chose qui a besoin
d'être « incassable » pour que toute la fonctionnalité le soit : un backend
bogué ne peut agir que via `is_action_permitted`, jamais autour.

Principe de conception (liste BLANCHE, jamais liste noire) : par défaut,
RIEN n'est autorisé. Une action n'est permise que si la fenêtre active
correspond explicitement à une entrée de `AllowlistPolicy.targets` ET que
cette entrée autorise cette nature d'action précise. Le denylist global
(mots de passe, élévation admin, gestionnaires d'identifiants...) prime
TOUJOURS sur une correspondance d'allowlist, sans exception ni option de
contournement — défense en profondeur contre une entrée utilisateur trop
large écrite par erreur."""

from __future__ import annotations

from apex.desktop.model import (
    READ_ONLY_ACTION_KINDS,
    AllowedTarget,
    AllowlistPolicy,
    DesktopAction,
    PermissionDecision,
    WindowInfo,
)

#: Sous-chaînes (comparaison insensible à la casse) TOUJOURS refusées,
#: quelle que soit la configuration utilisateur — fusionnées dans TOUTE
#: `AllowlistPolicy` par `build_policy`, jamais remplaçables. Couvre
#: FR/EN car une machine réelle mélange souvent les deux (OS localisé,
#: applications non traduites).
DEFAULT_DENYLIST_PATTERNS: tuple[str, ...] = (
    "password", "mot de passe", "passwort",
    "login", "se connecter", "sign in", "identifiants",
    "credential", "gestionnaire d'identification", "credential manager",
    "user account control", "contrôle de compte", "contrôle de compte d'utilisateur",
    "sudo", "administrateur", "elevation", "élévation",
    "windows security", "sécurité windows",
    "wallet", "portefeuille", "seed phrase", "phrase de récupération",
    "private key", "clé privée",
    "2fa", "authenticator", "authentification à deux facteurs", "one-time code", "code unique",
)


def _title_contains(haystack: str, needle: str) -> bool:
    return needle.strip().lower() in haystack.lower()


def _target_matches_window(target: AllowedTarget, window: WindowInfo) -> bool:
    if not _title_contains(window.title, target.title_pattern):
        return False
    return target.process_name is None or target.process_name.lower() == window.process_name.lower()


def build_policy(
    user_targets: tuple[AllowedTarget, ...] = (), *, extra_denylist_patterns: tuple[str, ...] = (),
) -> AllowlistPolicy:
    """Point d'entrée UNIQUE pour construire une `AllowlistPolicy` — fusionne
    toujours `DEFAULT_DENYLIST_PATTERNS`, jamais une construction directe de
    `AllowlistPolicy(denylist_title_patterns=...)` ailleurs dans le dépôt
    qui pourrait, par erreur, omettre le denylist de base."""
    return AllowlistPolicy(
        targets=user_targets,
        denylist_title_patterns=DEFAULT_DENYLIST_PATTERNS + tuple(extra_denylist_patterns),
    )


def is_action_permitted(
    action: DesktopAction, window: WindowInfo | None, policy: AllowlistPolicy,
) -> PermissionDecision:
    """La décision, et RIEN d'autre — ne journalise pas, n'exécute rien :
    `apex.desktop.provider` est responsable d'agir sur ce verdict et de le
    consigner. Garder cette fonction pure est ce qui permet de la tester de
    façon exhaustive sans jamais toucher un vrai écran/backend."""
    if action.kind in READ_ONLY_ACTION_KINDS:
        return PermissionDecision.ALLOWED

    if window is None:
        return PermissionDecision.DENIED_NO_ACTIVE_WINDOW

    if any(_title_contains(window.title, pattern) for pattern in policy.denylist_title_patterns):
        return PermissionDecision.DENIED_DENYLISTED

    matching = [t for t in policy.targets if _target_matches_window(t, window)]
    if not matching:
        return PermissionDecision.DENIED_NOT_ALLOWLISTED

    if any(action.kind in t.allowed_kinds for t in matching):
        return PermissionDecision.ALLOWED
    return PermissionDecision.DENIED_ACTION_KIND_NOT_PERMITTED
