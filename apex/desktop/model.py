"""Types du domaine `apex.desktop` — contrôle réel du clavier/souris de la
machine hôte, à la demande explicite (§« périmètre limité, sans confirmation
par action, mais jamais hors allowlist »).

Distinct de `apex.sandbox` par nature, pas seulement par nom : le sandbox
isole du code généré DE la machine hôte (namespaces/conteneur, réseau
coupé) ; `apex.desktop` agit, à l'inverse, DIRECTEMENT sur le vrai bureau —
vraies fenêtres, vrai presse-papiers, vraies applications connectées à de
vrais comptes. Ce n'est jamais un raffinement du sandbox, c'est une
frontière de confiance séparée, avec son propre consentement explicite
(`--allow-desktop-control`, Gate) et sa propre politique d'autorisation
(`apex.desktop.allowlist`) — jamais activée par défaut, jamais héritée d'un
autre `--allow-*`."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from apex.obs.capability import CapabilityStatus


class DesktopActionKind(str, Enum):
    """Vocabulaire FERMÉ — jamais un `execute(str)` arbitraire de type
    « exécute cette commande shell/AppleScript/PowerShell » : chaque nature
    d'action est explicitement listée ici, ce qui rend `apex.desktop.
    allowlist` capable d'autoriser par NATURE d'action, pas seulement par
    fenêtre cible."""

    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    TYPE_TEXT = "type_text"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"
    SCREENSHOT = "screenshot"
    WAIT = "wait"


#: Natures d'action qui ne LISENT jamais l'écran ni n'y agissent, seulement
#: une pause — toujours permises même dans le périmètre le plus restreint,
#: jamais soumises à l'allowlist de fenêtres (rien à autoriser : aucun
#: effet).
READ_ONLY_ACTION_KINDS = frozenset({DesktopActionKind.SCREENSHOT, DesktopActionKind.WAIT})


@dataclass(frozen=True)
class WindowInfo:
    """Snapshot de LA fenêtre active au moment de la décision — jamais une
    fenêtre nommée par l'appelant (qui pourrait mentir) : c'est toujours le
    backend réel (`DesktopAutomationProvider.active_window()`) qui la
    mesure."""

    title: str
    process_name: str
    is_focused: bool = True


@dataclass(frozen=True)
class DesktopAction:
    kind: DesktopActionKind
    #: Coordonnées écran pour CLICK/DOUBLE_CLICK/SCROLL — `None` pour les
    #: autres natures.
    x: int | None = None
    y: int | None = None
    #: Texte pour TYPE_TEXT, touche(s) pour KEY_PRESS (ex. "ctrl+c").
    text: str | None = None
    #: Delta de défilement (positif = vers le haut) pour SCROLL.
    scroll_amount: int | None = None
    #: Secondes pour WAIT.
    duration_s: float | None = None


class PermissionDecision(str, Enum):
    ALLOWED = "allowed"
    #: La fenêtre active ne correspond à AUCUNE entrée de l'allowlist —
    #: refus par défaut (liste blanche, jamais liste noire).
    DENIED_NOT_ALLOWLISTED = "denied_not_allowlisted"
    #: La fenêtre active correspond à une entrée du DENYLIST global — refus
    #: qui prime TOUJOURS sur une correspondance d'allowlist (défense en
    #: profondeur : un motif d'allowlist trop large ne peut jamais
    #: contourner le denylist).
    DENIED_DENYLISTED = "denied_denylisted"
    #: La fenêtre est autorisée, mais pas pour CETTE nature d'action précise
    #: (ex. entrée qui n'autorise que CLICK, jamais TYPE_TEXT).
    DENIED_ACTION_KIND_NOT_PERMITTED = "denied_action_kind_not_permitted"
    #: Aucune fenêtre active mesurée (backend indisponible/écran verrouillé)
    #: — refus par prudence, jamais une autorisation par défaut sur
    #: l'inconnu.
    DENIED_NO_ACTIVE_WINDOW = "denied_no_active_window"


@dataclass(frozen=True)
class AllowedTarget:
    """Une entrée d'allowlist : quelle fenêtre, quelles actions.

    `title_pattern` est comparé à `WindowInfo.title` (sous-chaîne, jamais un
    regex — une entrée mal écrite ne doit jamais élargir silencieusement la
    portée via une métacaractère regex non intentionnel). `process_name`
    est optionnel : quand fourni, les DEUX doivent correspondre (défense en
    profondeur contre un titre de fenêtre usurpé par une page web, par
    exemple)."""

    title_pattern: str
    allowed_kinds: frozenset[DesktopActionKind]
    process_name: str | None = None


@dataclass(frozen=True)
class AllowlistPolicy:
    """La politique complète, réellement appliquée par `is_action_permitted`
    — jamais une simple donnée de configuration décorative : c'est LA
    frontière de sécurité de ce module."""

    targets: tuple[AllowedTarget, ...] = ()
    #: Sous-chaînes de titre TOUJOURS refusées, même si une entrée de
    #: `targets` matcherait par ailleurs — jamais contournable depuis la
    #: config utilisateur (voir `DEFAULT_DENYLIST_PATTERNS`, fusionné dans
    #: `apex.desktop.allowlist.build_policy`, jamais remplaçable en entier).
    denylist_title_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesktopActionResult:
    action: DesktopAction
    decision: PermissionDecision
    #: `True` seulement si l'action a RÉELLEMENT été exécutée par le
    #: backend — jamais `True` pour une action refusée par la politique.
    executed: bool
    detail: str = ""
    facts: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AutomationCapabilityReport:
    status: CapabilityStatus
    backend: str
    detail: str
    facts: dict[str, object] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status is CapabilityStatus.AVAILABLE
