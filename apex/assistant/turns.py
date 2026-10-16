"""Tours de conversation ASYNCHRONES de l'assistant : l'interface envoie un message et suit le tour (file,
génération, appels d'outils, fin) sans bloquer une requête HTTP pendant les minutes qu'une génération CPU
peut prendre ; elle peut l'annuler.

Chaîne réelle d'un tour :
  message utilisateur (inséré) -> tour `queued` -> exécuteur (UNE génération à la fois : un seul modèle en
  mémoire) -> contexte (historique borné, mémoire d'APEX, pièces jointes, consignes du mode et du projet) ->
  routage (`apex.routing.select_model`, modèle préféré des paramètres) -> génération avec repli
  (`generate_with_fallback`, runtime natif compris) -> si le mode offre des outils : protocole JSON
  `{"tool": ..., "arguments": ...}` exécuté par `apex.tools.engine` (permissions explicites, sandbox réelle
  par `AgentRuntime` pour le code) puis observation rendue au modèle -> réponse insérée (`real`), ou échec
  explicite (`error`), ou annulation (`cancelled`) — jamais une réponse inventée à la place d'un échec.

Un tour interrompu par l'arrêt d'APEX est marqué comme tel au démarrage suivant (`recover_unfinished`).
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apex.assistant.modes import Mode, get_mode
from apex.assistant.settings import AppSettings, SettingsService
from apex.conversations.model import Message, MessageRole, MessageStatus

MAX_MESSAGE_CHARS = 20_000
_OBSERVATION_CHARS = 3_000
_STEPS_BUDGET_CHARS = 12_000
_PROSE_AROUND_TOOL_CALL = 200
_SANDBOX_CACHE_S = 300.0
_SPEAKERS = {MessageRole.USER: "Utilisateur", MessageRole.ASSISTANT: "Assistant"}

_PROJECT_ID = "<project_id rendu par game_studio.create>"

#: Rôle et exemple d'appel de chaque outil, montrés au modèle (un modèle 1,5B ne déduit pas l'usage d'un
#: schéma).
TOOL_GUIDE: dict[str, tuple[str, dict[str, Any]]] = {
    "fs.list": ("lister un dossier de l'espace de travail", {"path": "."}),
    "fs.read": ("lire un fichier", {"path": "main.py"}),
    "project.inspect": ("structure du projet (fichiers, tests)", {}),
    "fs.write": ("écrire un fichier COMPLET", {"path": "main.py", "content": "print('ok')\n"}),
    "fs.search": ("chercher un texte dans tout le projet (chemin, ligne)", {"query": "def charger", "glob": "*.py"}),
    "fs.edit": ("remplacer un passage EXACT d'un fichier, sans le réécrire",
                {"path": "main.py", "old": "print('ok')", "new": "print('prêt')"}),
    "code.run": ("exécuter un fichier Python dans la sandbox", {"path": "main.py"}),
    "tests.run": ("lancer les tests dans la sandbox", {"start_dir": "."}),
    "calc.evaluate": ("calcul exact", {"expression": "1250 * 12 - 300"}),
    "finance.projection": ("projection d'épargne année par année",
                           {"initial": 1000, "monthly_contribution": 200, "annual_rate_percent": 4, "years": 10}),
    "finance.loan": ("mensualité et coût d'un prêt", {"principal": 150000, "annual_rate_percent": 3.5, "years": 20}),
    "color.contrast": ("contraste WCAG texte/fond", {"foreground": "#1F1A14", "background": "#F4EDE1"}),
    "model3d.create": ("construire un modèle 3D et écrire ses fichiers (Blender, Unity, Unreal)",
                       {"generator": "vehicle", "kind": "tank", "length": 7, "color": "kaki"}),
    "game_studio.create": ("confier à Game Studio une création 3D complète (objet détaillé, scène) ; rend "
                           "aussitôt les identifiants de la mission, qui se poursuit en arrière-plan",
                           {"description": "Une scène de garage avec deux voitures et un établi."}),
    "game_studio.status": ("état d'une mission Game Studio et de ses étapes", {"project_id": _PROJECT_ID}),
    "game_studio.results": ("résultats d'une mission Game Studio terminée (fichiers et empreintes)",
                            {"project_id": _PROJECT_ID}),
    "game_studio.cancel": ("annuler une mission Game Studio", {"project_id": _PROJECT_ID}),
}


def _now() -> datetime:
    return datetime.now(UTC)


def compose_history(history: Sequence[Message], content: str, budget: int) -> tuple[str, int]:
    """Échanges RÉELS précédents (jamais un message en erreur, annulé ou vide), du plus ancien au plus récent,
    dans `budget` caractères (les plus récents d'abord), puis le nouveau message."""
    lines: list[str] = []
    remaining = budget
    for message in reversed(list(history)):
        speaker = _SPEAKERS.get(message.role)
        if speaker is None or message.status is not MessageStatus.REAL or not message.content.strip():
            continue
        text = message.content.strip()
        if len(text) > 2000:
            text = text[:2000] + " […]"
        line = f"{speaker} : {text}"
        if len(line) > remaining:
            break
        lines.append(line)
        remaining -= len(line)
    if not lines:
        return content, 0
    return ("Échanges précédents de cette conversation (du plus ancien au plus récent) :\n"
            + "\n".join(reversed(lines)) + f"\n\nNouveau message de l'utilisateur :\n{content}", len(lines))


def compose_turns(history: Sequence[Message], budget: int) -> list[tuple[str, str]]:
    """Les mêmes échanges que `compose_history`, en TOURS (`user`/`assistant`) pour les modèles qui
    connaissent la conversation : du plus ancien au plus récent, dans `budget` caractères."""
    turns: list[tuple[str, str]] = []
    remaining = budget
    for message in reversed(list(history)):
        role = {MessageRole.USER: "user", MessageRole.ASSISTANT: "assistant"}.get(message.role)
        if role is None or message.status is not MessageStatus.REAL or not message.content.strip():
            continue
        text = message.content.strip()
        if len(text) > 2000:
            text = text[:2000] + " […]"
        if len(text) > remaining:
            break
        turns.append((role, text))
        remaining -= len(text)
    turns.reverse()
    while turns and turns[0][0] != "user":
        turns.pop(0)  # une conversation commence par l'utilisateur
    return turns


def render_tools(names: Sequence[str]) -> str:
    lines = []
    for name in names:
        purpose, example = TOOL_GUIDE.get(name, (name, {}))
        lines.append(f"- {name} : {purpose}. Exemple : "
                     + json.dumps({"tool": name, "arguments": example}, ensure_ascii=False))
    return "\n".join(lines)


def system_prompt(mode: Mode, *, project_name: str | None, project_instructions: str, tools: Sequence[str],
                  notes: Sequence[str] = ()) -> str:
    parts = [mode.instructions]
    if project_name:
        parts.append(f"Cette conversation appartient au projet « {project_name} ».")
    if project_instructions.strip():
        parts.append("Consignes du projet (à respecter) :\n" + project_instructions.strip())
    if tools:
        parts.append(
            "OUTILS. Quand un outil est utile, réponds UNIQUEMENT par un objet JSON, sans autre texte :\n"
            '{"tool": "<nom>", "arguments": {...}}\n'
            "Tu recevras son résultat réel, puis tu continueras. Quand tu as terminé, réponds normalement à "
            "l'utilisateur (sans JSON d'outil) en t'appuyant sur les résultats obtenus.\nOutils disponibles :\n"
            + render_tools(tools))
    parts.extend(notes)
    return "\n\n".join(parts)


def parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Un appel d'outil est une réponse qui EST un objet JSON `{"tool": ..., "arguments": {...}}` (bloc ```
    toléré, à peine entouré de texte). Une réponse rédigée qui contient du JSON reste une réponse."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    start = stripped.find("{")
    if start < 0 or start > _PROSE_AROUND_TOOL_CALL:
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(stripped[start:])
    except json.JSONDecodeError:
        return None
    if len(stripped) - start - end > _PROSE_AROUND_TOOL_CALL:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("tool"), str):
        return None
    arguments = value.get("arguments", {})
    if not isinstance(arguments, dict) or set(value) - {"tool", "arguments"}:
        return None
    return value["tool"], arguments


def _brain_unsure(result: Any, threshold: float, *, question: str = "",
                  connected: Sequence[str] = ()) -> tuple[str, str] | None:
    """(motif, raison écrite) quand la réponse du Cerveau APEX ne doit pas être servie telle quelle.
    Seuil 0 : jamais. Les autres moteurs ne sont jamais déclinés (ils ne mesurent pas leur confiance ici).
    Motifs :

    - `low_confidence` : confiance mesurée sous le seuil → moteur suivant ;
    - `cannot_know` : sa phrase apprise « je ne peux pas le savoir » (`apex.brain.chat.cannot_know_kind`)
      → moteur suivant, qui peut savoir ;
    - question d'actualité (`apex.assistant.freshness`) et réponse « pas d'accès au temps réel » : aucun
      modèle ne la connaît. `current_info_tool` si un outil connecté est offert → moteur suivant, qui peut
      l'appeler ; sinon `current_info_no_tool` → la réponse honnête du Cerveau est GARDÉE."""
    from apex.assistant.freshness import asks_for_current_information

    facts = getattr(result, "facts", None) or {}
    if threshold <= 0 or facts.get("backend") != "apex_brain":
        return None
    if facts.get("cannot_know"):
        if facts.get("cannot_know_kind") == "realtime" and asks_for_current_information(question):
            if connected:
                return ("current_info_tool",
                        f"question d'actualité, outil connecté offert : {', '.join(connected)}")
            return ("current_info_no_tool",
                    "question d'actualité sans outil connecté : aucun modèle ne la connaît")
        return "cannot_know", "le Cerveau APEX dit ne pas pouvoir le savoir"
    confidence = facts.get("confidence")
    if confidence is None or confidence >= threshold:
        return None
    return "low_confidence", f"confiance du Cerveau APEX {confidence:.3f} sous le seuil {threshold:.2f}"


#: Ajouté aux consignes du moteur suivant quand une question d'actualité lui est confiée avec un outil
#: connecté.
_CURRENT_INFO_NOTE = ("Cette question porte sur une information actuelle : appelle l'outil connecté "
                      "offert et réponds d'après son résultat. Sans résultat de l'outil, dis que tu ne "
                      "peux pas le savoir.")


#: Verdicts de la vérification croisée qui prouvent qu'une des deux versions est fausse.
_CODE_MISMATCH = frozenset({"disagree", "answer_fails"})


def model3d_mismatch(facts: dict[str, Any], question: str) -> str | None:
    """Raison pour laquelle les modèles 3D construits par le Cerveau pendant sa réponse ne répondent PAS à
    la demande (sa réponse décrit alors un objet sans rapport), ou None. Mesuré : sur des demandes hors de
    son répertoire, il construit le plus souvent une autre sorte (docs/CERVEAU_APEX_V2.md §9 septies)."""
    from apex.assistant.creation import analyze
    from apex.brain import studio3d

    calls = [c for c in facts.get("tool_calls") or [] if c.get("tool") == "3d"]
    if not calls:
        return None
    if analyze(question).kind == "none":
        return "le Cerveau a construit un modèle 3D pour un message qui n'en demande pas"
    if any(not str(c.get("result", "")).startswith("erreur")
           and studio3d.matches_request(str(c.get("expression", "")), question) for c in calls):
        return None
    return "le modèle 3D construit par le Cerveau ne correspond pas à l'objet demandé"

#: Ajouté aux consignes quand `game_studio.create` est offert (Game Studio installé, missions permises).
_GAME_STUDIO_NOTE = ("GAME STUDIO. Pour une création 3D complète (objet détaillé, scène), appelle "
                     "game_studio.create avec la demande de l'utilisateur : la mission se poursuit en "
                     "arrière-plan, pendant des minutes. Ne dis jamais qu'elle est terminée, ni ce "
                     "qu'elle a produit, sans l'avoir lu avec game_studio.status ou game_studio.results.")
#: Bornes de `description` de `game_studio.create` (contrat de Game Studio).
_GAME_STUDIO_DESCRIPTION = (20, 4000)
#: Missions lancées au plus par tour (appel du modèle ou délégation du Cerveau).
_GAME_STUDIO_LAUNCHES_PER_TURN = 1


def _kind(expression: str) -> str:
    from apex.brain import studio3d

    try:
        return studio3d.parse(expression).kind
    except studio3d.Error3D:
        return ""


def game_studio_description(request: str) -> str:
    """La demande de l'utilisateur telle quelle (jamais réécrite par le modèle), dans les bornes de
    l'outil : une demande trop courte (« un tank kaki ») est seulement présentée comme telle."""
    text = request.strip()
    if len(text) < _GAME_STUDIO_DESCRIPTION[0]:
        text = f"Demande de l'utilisateur : {text}"
    return text[:_GAME_STUDIO_DESCRIPTION[1]]


def _generate(ranked: list[Any], *, delegate_below: float, deadline: float, cancel: threading.Event,
              question: str = "", connected: Sequence[str] = (),
              verify: Callable[[Any, dict[str, Any], Any], dict[str, Any] | None] | None = None,
              **request: Any) -> tuple[Any, dict[str, Any] | None, dict[str, Any] | None]:
    """`generate_with_fallback` (inchangé) sur les candidats classés. Mode automatique : une réponse du
    Cerveau APEX peu sûre d'elle, qui dit ne pas pouvoir savoir, ou dont le code diverge de sa propre
    version Python (`verify`, vérification croisée dans la sandbox), est confiée aux candidats suivants, par
    un second appel. La réponse déclinée reste dans la trace (tentative non retenue, raison écrite) ; si
    aucun suivant ne répond, elle est gardée, et c'est dit. Rend (génération, délégation ou None,
    vérification croisée ou None) ; la vérification est rendue même sans délégation (moteur « Cerveau »
    choisi, seuil 0)."""
    from apex.routing.generation import FallbackResult, GenerationAttempt, generate_with_fallback

    first = generate_with_fallback(ranked, timeout_s=deadline - time.monotonic(), cancel_event=cancel,
                                   **request)
    brain, answer = first.succeeded_with, first.result
    unsure = _brain_unsure(answer, delegate_below, question=question, connected=connected)
    checked = None
    if (unsure is None and verify is not None and brain is not None and answer is not None
            and (answer.facts or {}).get("backend") == "apex_brain"):
        checked = verify(brain, request, answer)
        if checked is not None and checked["status"] in _CODE_MISMATCH and delegate_below > 0:
            unsure = ("code_mismatch", f"vérification croisée : {checked['detail']}")
    if (unsure is None and delegate_below > 0 and answer is not None
            and (answer.facts or {}).get("backend") == "apex_brain"):
        mismatch = model3d_mismatch(answer.facts or {}, question)
        if mismatch is not None:
            unsure = ("model3d_mismatch", mismatch)
    if brain is None or answer is None or unsure is None:
        return first, None, checked
    motive, reason = unsure
    delegation = {"from": brain.ref.id, "reason": motive, "confidence": answer.facts.get("confidence"),
                  "threshold": delegate_below}
    if motive == "current_info_no_tool":
        return first, {**delegation, "to": None, "status": "kept"}, checked
    if motive == "current_info_tool":
        request = {**request, "system": f"{request.get('system') or ''}\n\n{_CURRENT_INFO_NOTE}".strip()}
    declined = GenerationAttempt(candidate=brain, succeeded=False, error=f"déclinée : {reason}",
                                 result=answer)
    later = ranked[ranked.index(brain) + 1:]
    second = None
    if later and not cancel.is_set() and deadline - time.monotonic() > 1:
        second = generate_with_fallback(later, timeout_s=deadline - time.monotonic(), cancel_event=cancel,
                                        **request)
    attempts = (*first.attempts[:-1], declined, *(second.attempts if second is not None else ()))
    if second is not None and second.result is not None and second.succeeded_with is not None:
        return (FallbackResult(result=second.result, succeeded_with=second.succeeded_with,
                               attempts=attempts),
                {**delegation, "to": second.succeeded_with.ref.id, "status": "delegated"}, checked)
    return (FallbackResult(result=answer, succeeded_with=brain, attempts=attempts),
            {**delegation, "to": None, "status": "kept"}, checked)


#: Résultats d'outils rendus en entier au modèle pendant un tour (les plus récents) ; les plus anciens sont
#: résumés en une ligne chacun (`_step_line`).
_DETAILED_OBSERVATIONS = 6


def _step_line(step: dict[str, Any]) -> str:
    arguments = step.get("arguments") or {}
    target = next((str(arguments[k]) for k in ("path", "start_dir", "expression", "kind") if k in arguments), "")
    outcome = "réussi" if step.get("ok") else "échec"
    return f"{step.get('step')}. {step.get('tool')} {target[:80]} -> {outcome}".replace("  ", " ")


def _clip(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} caractères omis]"


@dataclass
class TurnView:
    id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str | None
    mode: str
    status: str
    steps: list[dict[str, Any]]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_row(cls, row: Any) -> TurnView:
        return cls(id=row.id, conversation_id=row.conversation_id, user_message_id=row.user_message_id,
                   assistant_message_id=row.assistant_message_id, mode=row.mode, status=row.status,
                   steps=list(row.steps or []), error=row.error, created_at=row.created_at,
                   started_at=row.started_at, finished_at=row.finished_at)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "conversation_id": self.conversation_id, "user_message_id": self.user_message_id,
                "assistant_message_id": self.assistant_message_id, "mode": self.mode, "status": self.status,
                "steps": self.steps, "error": self.error, "created_at": self.created_at.isoformat(),
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "finished_at": self.finished_at.isoformat() if self.finished_at else None}


class TurnBusy(RuntimeError):
    pass


class TurnManager:
    """Un par processus (`apex serve`). `provider_factory`, `config`, `sandbox_selection` et
    `game_studio_service` sont injectables pour les tests ; par défaut : les fournisseurs, la configuration,
    la sandbox et Game Studio RÉELS d'APEX."""

    def __init__(self, session_factory: Any, *, provider_factory: Callable[..., Any] | None = None,
                 config: Any = None, sandbox_selection: Callable[[], Any] | None = None,
                 synchronous: bool = False, game_studio_service: Any = None) -> None:
        self._session_factory = session_factory
        self._provider_factory = provider_factory
        self._config = config
        self._sandbox_selection = sandbox_selection
        #: Service de Game Studio passé à ses outils (tests) ; par défaut, celui du processus.
        self._game_studio_service = game_studio_service
        self._synchronous = synchronous
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apex-turn")
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        #: Sérialise « vérifier qu'aucun tour n'est actif, puis créer le tour » : deux envois simultanés
        #: dans la même conversation ne créent jamais deux tours concurrents.
        self._submit_lock = threading.Lock()
        self._sandbox_cache: tuple[float, Any] | None = None
        #: Sondes de la vérification croisée (langage -> prêt, détail, instant) : un langage que la sandbox
        #: de ce PC n'exécute pas n'est sondé qu'une fois toutes les 10 minutes.
        self._crosscheck_probes: dict[str, tuple[bool, str, float]] = {}

    # -- interface publique ----------------------------------------------------------------------------

    def submit(self, conversation_id: str, content: str) -> tuple[Message, TurnView]:
        from apex.conversations.repository import ConversationRepository, MessageRepository
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository
        from apex.policy.engine import PolicyEngine
        from apex.policy.model import Action, Subject

        PolicyEngine().authorize(Subject.HUMAN, Action.SEND_MESSAGE)
        if not content.strip():
            raise ValueError("le message est vide")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message trop long ({len(content)} caractères, maximum {MAX_MESSAGE_CHARS})")
        conversation = ConversationRepository(self._session_factory).get(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation « {conversation_id} » introuvable")
        mode = get_mode(conversation.mode)
        with self._submit_lock:
            if self.busy([conversation_id]):
                raise TurnBusy("une réponse est déjà en cours dans cette conversation : attendez-la ou annulez-la")
            user_message = MessageRepository(self._session_factory).create(
                conversation_id=conversation_id, role=MessageRole.USER, content=content, model_id=None,
                provider_id=None, status=MessageStatus.REAL, error=None, meta={"mode": mode.id})
            ConversationRepository(self._session_factory).touch(conversation_id)
            turn_id = str(uuid.uuid4())
            with session_scope(self._session_factory) as session:
                row = ConversationTurnRepository(session).create(
                    id=turn_id, conversation_id=conversation_id, user_message_id=user_message.id, mode=mode.id,
                    created_at=_now())
                view = TurnView.from_row(row)
            with self._lock:
                self._cancels[turn_id] = threading.Event()
        if self._synchronous:
            self._safe_execute(turn_id)
            view = self.get(turn_id) or view
        else:
            self._executor.submit(self._safe_execute, turn_id)
        return user_message, view

    def busy(self, conversation_ids: list[str]) -> bool:
        """Vrai si l'une de ces conversations a un tour en file ou en cours (suppression refusée, nouvel envoi
        refusé) : une conversation n'est jamais supprimée sous un tour qui écrit encore dedans."""
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository

        with session_scope(self._session_factory) as session:
            repo = ConversationTurnRepository(session)
            return any(t.status in ("queued", "running")
                       for conversation_id in conversation_ids for t in repo.by_conversation(conversation_id, limit=5))

    def get(self, turn_id: str) -> TurnView | None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository

        with session_scope(self._session_factory) as session:
            row = ConversationTurnRepository(session).get(turn_id)
            return TurnView.from_row(row) if row is not None else None

    def by_conversation(self, conversation_id: str) -> list[TurnView]:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository

        with session_scope(self._session_factory) as session:
            return [TurnView.from_row(r) for r in ConversationTurnRepository(session).by_conversation(conversation_id)]

    def cancel(self, turn_id: str) -> bool:
        """Demande l'annulation : avant son début, le tour ne démarre pas ; pendant, la génération s'arrête entre
        deux tokens et aucun autre outil n'est appelé. Faux si le tour est déjà terminé ou inconnu."""
        view = self.get(turn_id)
        if view is None or view.status not in ("queued", "running"):
            return False
        with self._lock:
            event = self._cancels.get(turn_id)
        if event is None:
            return False
        event.set()
        return True

    def recover_unfinished(self) -> int:
        """Au démarrage : un tour resté `queued`/`running` a été interrompu par l'arrêt d'APEX — il est clos
        en échec explicite, avec un message d'erreur visible dans la conversation."""
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository

        with session_scope(self._session_factory) as session:
            stale = [TurnView.from_row(r) for r in ConversationTurnRepository(session).unfinished()]
        for turn in stale:
            self._finish(turn.id, turn.conversation_id, status="failed", message_status=MessageStatus.ERROR,
                         content="", error="interrompu : APEX s'est arrêté pendant la génération. Renvoyez le message.",
                         meta={"mode": turn.mode, "steps": turn.steps})
        return len(stale)

    def shutdown(self) -> None:
        with self._lock:
            for event in self._cancels.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- exécution ---------------------------------------------------------------------------------------

    def _safe_execute(self, turn_id: str) -> None:
        try:
            self._execute(turn_id)
        except Exception as exc:  # jamais un tour bloqué « en cours » par une exception non prévue
            view = self.get(turn_id)
            if view is not None and view.status in ("queued", "running"):
                self._finish(turn_id, view.conversation_id, status="failed", message_status=MessageStatus.ERROR,
                             content="", error=f"erreur interne d'APEX : {exc.__class__.__name__}: {exc}"[:600],
                             meta={"mode": view.mode, "steps": view.steps})
        finally:
            with self._lock:
                self._cancels.pop(turn_id, None)

    def _execute(self, turn_id: str) -> None:
        from apex.conversations.repository import ConversationRepository, MessageRepository
        from apex.conversations.service import ConversationService

        turn = self.get(turn_id)
        if turn is None:
            return
        with self._lock:
            cancel = self._cancels.get(turn_id) or threading.Event()
        conversation = ConversationRepository(self._session_factory).get(turn.conversation_id)
        if conversation is None:  # supprimée pendant l'attente : rien à répondre
            self._update(turn_id, status="cancelled", error="conversation supprimée", finished_at=_now())
            return
        mode = get_mode(turn.mode)
        if cancel.is_set():
            self._finish(turn_id, conversation.id, status="cancelled", message_status=MessageStatus.CANCELLED,
                         content="", error="annulée avant le début de la génération", meta={"mode": mode.id})
            return
        self._update(turn_id, status="running", started_at=_now())
        started = time.monotonic()
        settings, _problems = SettingsService(self._session_factory).load()
        deadline = started + settings.runtime.generation_timeout_s
        messages = MessageRepository(self._session_factory).recent_by_conversation(conversation.id, limit=200)
        user_message = next((m for m in messages if m.id == turn.user_message_id), None)
        if user_message is None:
            self._finish(turn_id, conversation.id, status="failed", message_status=MessageStatus.ERROR, content="",
                         error="message utilisateur introuvable", meta={"mode": mode.id})
            return
        content = user_message.content
        history = [m for m in messages if m.id != user_message.id and m.created_at <= user_message.created_at]
        prompt, history_used = compose_history(history, content, settings.behavior.history_chars)
        turns = compose_turns(history, settings.behavior.history_chars)
        conversations = ConversationService(self._session_factory)
        recalled: list[Any] = []
        context_blocks: list[str] = []
        if settings.behavior.use_memory:
            memory_context, recalled = conversations.recall_context(conversation.id, content)
            if memory_context:
                prompt = f"{memory_context}\n\n{prompt}"
                context_blocks.append(memory_context)
        project = None
        attachments: list[str] = []
        if conversation.project_id:
            from apex.projects.service import ProjectService

            project = ProjectService(self._session_factory).get(conversation.project_id)
            if project is not None and settings.privacy.attachments_enabled:
                from apex.assistant.attachments import AttachmentService

                context, attachments = AttachmentService(self._session_factory).render_context(project.id)
                if context:
                    prompt = f"{context}\n\n{prompt}"
                    context_blocks.insert(0, context)
        last_user = "\n\n".join([*context_blocks, content])
        tools = self._tools(mode, conversation, settings, turn_id)
        offered = tools["offered"] if tools else []
        from apex.assistant.freshness import connected_tools

        connected = (connected_tools(tools["engine"].registry.get(name)[0] for name in offered)
                     if tools else [])
        verify = self._code_verifier(settings, turn_id, deadline)
        system = system_prompt(mode, project_name=project.name if project else None,
                               project_instructions=project.instructions if project else "", tools=offered,
                               notes=[*tools["notes"], *tools["guidance"]] if tools else ())
        meta: dict[str, Any] = {
            "mode": mode.id, "history_messages": history_used, "attachments": attachments,
            "memory": [{"id": r.record.id, "source": r.record.source, "score": round(r.score, 4)} for r in recalled],
            "tools_offered": offered, "tools_notes": tools["notes"] if tools else [],
        }

        ranked, refusal = self._candidates(settings)
        # Délégation (mode automatique seulement) : le Cerveau sait quand il n'est pas sûr de lui.
        delegation_threshold = (settings.models.brain_delegate_below if settings.models.engine == "auto"
                                else 0.0)
        if not ranked:
            self._finish(turn_id, conversation.id, status="failed", message_status=MessageStatus.ERROR, content="",
                         error=f"aucun modèle disponible : {refusal}", meta={**meta, "steps": []})
            return

        steps: list[dict[str, Any]] = []
        observations: list[str] = []
        #: Modèles 3D construits par le Cerveau APEX PENDANT sa génération (outil interne,
        #: `apex.brain.studio3d`) : leurs fichiers sont écrits après, par l'outil `model3d.create`, avec les
        #: permissions de ce tour.
        brain_models: list[dict[str, Any]] = []
        #: Appels 3D du Cerveau, réussis ou non : il a reconnu une demande 3D (délégation à Game Studio).
        brain_3d = False
        final_text: str | None = None
        served = None
        last_result: Any = None
        tokens = {"prompt": 0, "completion": 0}
        max_steps = settings.tools.max_tool_steps if offered else 0
        for step_number in range(1, max_steps + 2):
            if cancel.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                self._finish(turn_id, conversation.id, status="failed", message_status=MessageStatus.ERROR,
                             content="", error=(f"délai dépassé ({settings.runtime.generation_timeout_s} s) : "
                                                "augmentez-le dans Paramètres > Runtime, ou demandez une réponse "
                                                "plus courte"),
                             meta={**meta, "steps": steps})
                return
            last_call = offered and step_number > max_steps
            step_prompt = prompt
            step_user = last_user
            if observations:
                suffix = ""
                if len(steps) > _DETAILED_OBSERVATIONS:
                    # Long tour (gros projet) : chaque étape plus ancienne reste connue du modèle, en une
                    # ligne (outil, fichier visé, réussite) — il n'oublie pas ce qu'il a déjà écrit ou
                    # testé.
                    suffix = ("\n\nÉtapes précédentes de ce tour (résumé) :\n"
                              + "\n".join(_step_line(s) for s in steps[:-_DETAILED_OBSERVATIONS]))
                suffix += ("\n\nOutils déjà utilisés pendant ce tour (résultats réels) :\n"
                           + "\n".join(observations[-_DETAILED_OBSERVATIONS:]))
                suffix += ("\n\nLimite d'outils atteinte : réponds maintenant à l'utilisateur, sans outil."
                           if last_call else
                           "\n\nContinue : appelle un autre outil si nécessaire, sinon réponds à l'utilisateur.")
                step_prompt += suffix
                step_user += suffix
            kwargs: dict[str, Any] = {}
            if self._provider_factory is not None:
                kwargs["provider_factory"] = self._provider_factory
            # Délégation à la première étape seulement : elle choisit le modèle de tout le tour.
            outcome, delegation, checked = _generate(
                ranked, delegate_below=delegation_threshold if step_number == 1 else 0.0, deadline=deadline,
                cancel=cancel, question=content, connected=connected,
                verify=verify if step_number == 1 else None, prompt=step_prompt, system=system,
                max_tokens=settings.models.max_tokens, temperature=settings.models.temperature,
                messages=(*turns, ("user", step_user)), **kwargs)
            if delegation is not None:
                meta["delegation"] = delegation
            if checked is not None:
                meta["crosscheck"] = checked
            if outcome.result is None:
                if cancel.is_set():
                    break
                detail = "; ".join(f"{a.candidate.ref.id} : {a.error}" for a in outcome.attempts)
                self._finish(turn_id, conversation.id, status="failed", message_status=MessageStatus.ERROR,
                             content="", error=f"génération impossible — {detail}"[:2000], meta={**meta, "steps": steps})
                return
            served = outcome.succeeded_with
            ranked = [served]  # le même modèle pour tout le tour
            last_result = outcome.result
            calls_3d = [c for c in (getattr(outcome.result, "facts", None) or {}).get("tool_calls", [])
                        if c.get("tool") == "3d"]
            brain_3d = brain_3d or bool(calls_3d)
            brain_models.extend(c for c in calls_3d if not str(c.get("result", "")).startswith("erreur"))
            for key, attr in (("prompt", "tokens_prompt"), ("completion", "tokens_completion")):
                measured = getattr(outcome.result, attr, None)
                if measured is not None and getattr(measured.status, "value", "") == "real":
                    tokens[key] += int(measured.value)
            call = parse_tool_call(outcome.result.text) if offered else None
            if call is None:
                final_text = outcome.result.text.strip()
                break
            if last_call:  # encore un appel d'outil après la limite : jamais présenté comme une réponse
                final_text = None
                break
            name, arguments = call
            step = self._run_tool(tools, name, arguments, step_number, turn_id)
            steps.append(step)
            observations.append(f"[{name}] {_clip(arguments, 400)} -> {_clip(step['observation'], _OBSERVATION_CHARS)}")
            self._update(turn_id, steps=steps)

        if brain_models and final_text and not cancel.is_set():
            self._write_brain_models(tools, brain_models, steps, meta, turn_id, content)
        if final_text and not cancel.is_set():
            brain_served = (getattr(last_result, "facts", None) or {}).get("backend") == "apex_brain"
            self._delegate_to_game_studio(tools, content, steps, meta, turn_id, brain_served=brain_served,
                                          brain_3d=brain_3d, user_message_id=user_message.id)
        facts = getattr(last_result, "facts", None) or {}
        meta.update({
            "steps": steps, "tokens_prompt": tokens["prompt"] or None, "tokens_completion": tokens["completion"] or None,
            "elapsed_s": round(time.monotonic() - started, 2),
            "runtime": {k: facts.get(k) for k in ("backend", "device", "dtype", "tokens_per_s", "loaded_now")
                        if k in facts},
        })
        model_id = served.ref.id if served is not None else None
        provider_id = (f"{served.endpoint.runtime}:{served.endpoint.served_model}"
                       if served is not None and served.endpoint is not None else None)
        if cancel.is_set():
            self._finish(turn_id, conversation.id, status="cancelled", message_status=MessageStatus.CANCELLED,
                         content="", error="génération annulée par l'utilisateur", meta=meta, model_id=model_id,
                         provider_id=provider_id)
            return
        if not final_text:
            self._finish(turn_id, conversation.id, status="failed", message_status=MessageStatus.ERROR, content="",
                         error=("aucune réponse finale : le modèle n'a produit que des appels d'outils ou un texte "
                                "vide" if steps else "le modèle a rendu une réponse vide"),
                         meta=meta, model_id=model_id, provider_id=provider_id)
            return
        message = self._finish(turn_id, conversation.id, status="done", message_status=MessageStatus.REAL,
                               content=final_text, error=None, meta=meta, model_id=model_id, provider_id=provider_id)
        conversations._record_event(conversation.id, "conversation.message_received", {
            "model_id": model_id, "history_messages_sent": history_used, "assistant_message_id": message.id,
            "use_memory": settings.behavior.use_memory, "memory": meta["memory"], "mode": mode.id,
            "tools_called": [s["tool"] for s in steps]})
        if settings.behavior.use_memory and settings.privacy.index_conversations:
            conversations.remember(conversation.id, message.id, content, final_text)

    # -- briques ------------------------------------------------------------------------------------------

    def _load_config(self) -> Any:
        if self._config is not None:
            return self._config
        from apex.config.loader import load_config

        return load_config()

    def _candidates(self, settings: AppSettings) -> tuple[list[Any], str]:
        from apex.assistant.engines import INFO, candidates_for
        from apex.routing.model import RoutingRequest
        from apex.routing.router import select_model

        engine = settings.models.engine
        candidates = candidates_for(engine, self._load_config())
        if not candidates:
            return [], f"aucun modèle déclaré pour le moteur « {INFO[engine].label} »"
        preferred = settings.models.preferred_model
        if preferred and preferred != "auto":
            chosen = [c for c in candidates if c.ref.id == preferred]
            if not chosen:
                return [], (f"le modèle choisi dans les Paramètres (« {preferred} ») n'appartient pas au "
                            f"moteur « {INFO[engine].label} »")
            candidates = chosen
        if engine == "brain":
            return candidates, ""  # choix explicite d'un modèle unique : rien à router
        # Automatique : le Cerveau APEX (s'il sait converser) d'abord, les modèles déclarés routés en repli.
        brain = [c for c in candidates if c.endpoint is not None and c.endpoint.runtime == "apex_brain"]
        others = [c for c in candidates if c not in brain]
        if not others:
            return brain, ""
        decision = select_model(others, RoutingRequest())
        if decision.selected is None:
            return (brain, "") if brain else ([], decision.reason)
        return brain + [sc.candidate for sc in decision.ranked_candidates if sc.score > 0.0], ""

    def _sandbox(self) -> Any:
        """Sélection RÉELLE de la sandbox (sondée), gardée 5 minutes : la sonde lance des processus."""
        now = time.monotonic()
        if self._sandbox_cache is not None and now - self._sandbox_cache[0] < _SANDBOX_CACHE_S:
            return self._sandbox_cache[1]
        if self._sandbox_selection is not None:
            selection = self._sandbox_selection()
        else:
            from apex.sandbox.provider import select_sandbox_provider

            selection = select_sandbox_provider()
        self._sandbox_cache = (now, selection)
        return selection

    def _tools(self, mode: Mode, conversation: Any, settings: AppSettings, turn_id: str) -> dict[str, Any] | None:
        """Outils réellement offerts dans CE tour : ceux du mode, moins ce que les paramètres retirent et ce que
        la machine ne permet pas (sandbox non vérifiée) — chaque retrait est dit au modèle et à l'interface."""
        if not mode.tools:
            return None
        from apex.assistant.modes import GAME_STUDIO_LAUNCH
        from apex.assistant.tools import assistant_registry
        from apex.assistant.workspace import conversation_workspace
        from apex.tools.engine import ToolContext, ToolEngine
        from apex.tools.model import Permission

        granted = set(mode.permissions)
        notes: list[str] = []
        if not settings.tools.game_studio_missions:
            granted -= GAME_STUDIO_LAUNCH  # lancer/annuler : seulement si l'utilisateur l'a permis
        if Permission.WRITE_WORKSPACE in granted and not settings.tools.workspace_write:
            granted.discard(Permission.WRITE_WORKSPACE)
            notes.append("L'écriture de fichiers est désactivée dans les Paramètres : propose les modifications "
                         "dans ta réponse au lieu de les écrire.")
        run_in_sandbox = None
        if Permission.EXECUTE_SANDBOX in granted:
            if not settings.tools.code_execution:
                granted.discard(Permission.EXECUTE_SANDBOX)
                notes.append("L'exécution de code est désactivée dans les Paramètres : ne prétends pas avoir "
                             "exécuté quoi que ce soit.")
            else:
                selection = self._sandbox()
                report = getattr(selection, "report", None)
                if report is None or not report.is_available:
                    granted.discard(Permission.EXECUTE_SANDBOX)
                    reason = getattr(report, "detail", "sandbox non vérifiée")
                    notes.append(f"Exécution de code indisponible sur ce PC ({reason}) : ne prétends pas avoir "
                                 "exécuté quoi que ce soit.")
                else:
                    run_in_sandbox = self._sandbox_runner(selection.provider, turn_id)
        problems: list[str] = []
        engine = ToolEngine(assistant_registry(problems=problems))
        frozen = frozenset(granted)
        offered = [name for name in mode.tools
                   if (spec := engine.registry.get(name)) is not None and spec[0].permissions <= frozen]
        workspace = conversation_workspace(conversation.id, conversation.project_id)
        service = self._game_studio_service
        extra = {} if service is None else {"game_studio_service": service}
        context = ToolContext(workspace=workspace, granted=frozen, run_in_sandbox=run_in_sandbox,
                              extra=extra)
        game_studio = None
        if any(name.startswith("game_studio.") for name in mode.tools):
            # Appartenance des missions : l'espace de travail de la conversation (ou du projet), contrôlée
            # par Game Studio lui-même ; calcul : le choix de l'utilisateur, imposé à chaque lancement.
            game_studio = {"absent": problems[0] if problems else None,
                           "missions": settings.tools.game_studio_missions,
                           "compute": settings.tools.game_studio_compute}
        # Consignes pour le modèle seulement (les `notes` sont aussi montrées à l'utilisateur).
        guidance = [_GAME_STUDIO_NOTE] if "game_studio.create" in offered else []
        return {"engine": engine, "context": context, "offered": offered, "notes": notes,
                "guidance": guidance, "game_studio": game_studio}

    def _code_verifier(
        self, settings: AppSettings, turn_id: str, deadline: float,
    ) -> Callable[[Any, dict[str, Any], Any], dict[str, Any] | None] | None:
        """Vérification croisée du code du Cerveau (`apex.assistant.verification`), seulement si l'exécution
        de code est permise (Paramètres > Outils) et la sandbox vérifiée sur ce PC : elle exécute du code.
        La sandbox n'est sondée que pour une réponse qui contient du code vérifiable."""
        if not settings.tools.code_execution:
            return None
        from apex.brain import crosscheck
        from apex.models.model import GenerationRequest
        from apex.models.provider import get_default_provider

        factory = self._provider_factory or (lambda candidate: get_default_provider(candidate.endpoint))

        def verify(candidate: Any, request: dict[str, Any], result: Any) -> dict[str, Any] | None:
            answer = crosscheck.parse_answer(result.text)
            if answer is None or answer.language == "python" or candidate.endpoint is None:
                return None
            provider = factory(candidate)
            if not hasattr(provider, "python_twin"):
                return None
            execute = self._sandbox_executor(turn_id)
            if execute is None:
                return {"language": answer.language, "status": "unverifiable", "example": None,
                        "detail": "sandbox non vérifiée sur ce PC : code non exécuté"}
            twin_request = GenerationRequest(
                endpoint=candidate.endpoint, prompt=request.get("prompt", ""), system=request.get("system"),
                messages=request.get("messages"), timeout_s=max(1.0, deadline - time.monotonic()))
            from apex.assistant import verification

            verdict = verification.check(
                result.text, lambda prefix: provider.python_twin(twin_request, prefix), execute,
                probes=self._crosscheck_probes)
            return None if verdict is None else {"language": answer.language, **verdict.to_dict()}

        return verify

    def _sandbox_executor(self, turn_id: str) -> Any:
        """Exécuteur de la vérification croisée dans la sandbox vérifiée de ce PC, ou None."""
        selection = self._sandbox()
        report = getattr(selection, "report", None)
        if report is None or not report.is_available or getattr(selection, "provider", None) is None:
            return None
        from apex.agents.runtime import AgentRuntime
        from apex.assistant import verification
        from apex.sandbox.model import ResourceLimits
        from apex.taskloop.runs import execution_pool

        config = self._load_config()
        return verification.sandbox_executor(verification.sandbox_run(
            AgentRuntime(self._session_factory), selection.provider, execution_pool(config),
            ResourceLimits.from_config(config.sandbox), label=f"crosscheck:{turn_id}"))

    def _sandbox_runner(self, provider: Any, turn_id: str) -> Any:
        from apex.agents.runtime import AgentRuntime
        from apex.policy.model import Subject
        from apex.sandbox.model import ResourceLimits
        from apex.taskloop.runs import execution_pool
        from apex.tools.sandbox_runner import sandbox_runner

        config = self._load_config()
        # Même chaîne que le TaskLoop : CORE orchestre l'exécution demandée par un humain, dans un clone
        # RESEARCH tracé (PolicyEngine, journal, audit), réseau toujours coupé.
        return sandbox_runner(agent_runtime=AgentRuntime(self._session_factory), sandbox=provider,
                              pool=execution_pool(config), base_limits=ResourceLimits.from_config(config.sandbox),
                              requested_by=Subject.CORE, clone_subject=Subject.RESEARCH,
                              label=f"assistant:{turn_id}")

    def _write_brain_models(self, tools: dict[str, Any] | None, calls: list[dict[str, Any]],
                            steps: list[dict[str, Any]], meta: dict[str, Any], turn_id: str,
                            request: str = "") -> None:
        """Écrit les fichiers des modèles 3D que le Cerveau a construits — seulement pour une demande de
        création, seulement l'objet demandé (`studio3d.matches_request`), et seulement si ce tour offre
        l'outil `model3d.create` (permission d'écriture). Tout ce qui n'est pas écrit est dit."""
        from apex.assistant.creation import analyze
        from apex.brain import studio3d

        def note(text: str) -> None:
            meta["tools_notes"] = [*meta.get("tools_notes", []), text]

        if analyze(request).kind == "none":
            note("Message sans demande de création : le modèle 3D construit par le Cerveau n'a pas été "
                 "écrit.")
            return
        unrelated = [c for c in calls if not studio3d.matches_request(c["expression"], request)]
        if unrelated:
            kinds = sorted({studio3d.LABELS.get(_kind(c["expression"]), "inconnu") for c in unrelated})
            note(f"Ébauche du Cerveau sans rapport avec la demande ({', '.join(kinds)}) : non écrite.")
        calls = [c for c in calls if c not in unrelated]
        if not calls:
            return
        if not tools or "model3d.create" not in tools["offered"]:
            meta["tools_notes"] = [*meta.get("tools_notes", []), (
                f"Le Cerveau APEX a construit {len(calls)} modèle(s) 3D, mais leurs fichiers n'ont pas été écrits : "
                "passez la conversation en mode 3D (ou Code) pour les obtenir.")]
            return
        for call in calls[:4]:
            try:
                arguments = studio3d.parse(call["expression"]).arguments()
            except studio3d.Error3D:
                continue
            step = self._run_tool(tools, "model3d.create", arguments, len(steps) + 1, turn_id)
            step["source"] = "cerveau"
            steps.append(step)
        self._update(turn_id, steps=steps)

    def _delegate_to_game_studio(self, tools: dict[str, Any] | None, request: str,
                                 steps: list[dict[str, Any]], meta: dict[str, Any], turn_id: str, *,
                                 brain_served: bool, brain_3d: bool, user_message_id: str) -> None:
        """Ce que le Cerveau fait d'un message du mode 3D quand Game Studio fait partie du mode. Son analyse
        d'intention (`apex.assistant.creation`) décide, jamais une intention devinée :

        - demande de création claire, lancement automatique permis par l'utilisateur (Paramètres >
          Outils) : mission lancée par `game_studio.create`, la demande de l'utilisateur pour description
          (quel que soit le moteur qui a rédigé la réponse : la décision est celle de l'analyse) ;
        - demande claire sans cette autorisation, ou demande ambiguë : mission PROPOSÉE ; l'utilisateur
          la lance d'un clic (`apex.assistant.game_studio.launch_proposed`, acte humain) ;
        - Game Studio absent : rien n'est proposé, et la raison est dite ;
        - sinon (question, commande sur l'existant…) : rien.

        L'appel 3D appris du Cerveau est rapporté comme indice, sans décider : seul, il se déclenche aussi
        sur des questions (docs/CERVEAU_APEX_V2.md §9 septies). Une mission au plus par tour."""
        from apex.assistant.creation import analyze

        info = (tools or {}).get("game_studio")
        if tools is None or info is None:
            return
        if any(s.get("tool") == "game_studio.create" for s in steps):
            return  # déjà confiée pendant ce tour (par un modèle qui appelle les outils)
        intent = analyze(request)
        if intent.kind == "none":
            return
        record: dict[str, Any] = {"trigger": "cerveau", "intent": intent.kind,
                                  "intent_reason": intent.reason, "brain_3d_call": brain_3d,
                                  "request_message_id": user_message_id, "launched": False}
        if info["absent"]:
            meta["game_studio"] = {**record, "state": "unavailable", "reason": info["absent"]}
            note = ("Demande de création 3D reconnue, mais Game Studio n'est pas disponible : "
                    f"{info['absent']}.")
            meta["tools_notes"] = [*meta.get("tools_notes", []), note]
            return
        if intent.kind == "ambiguous":
            meta["game_studio"] = {**record, "state": "proposed",
                                   "reason": f"demande à confirmer ({intent.reason})"}
            return
        record["served_by_brain"] = brain_served
        if "game_studio.create" not in tools["offered"]:
            reason = ("le lancement automatique des missions n'est pas activé : confirmez pour lancer"
                      if not info["missions"]
                      else "lancement non offert dans ce tour : confirmez pour lancer")
            meta["game_studio"] = {**record, "state": "proposed", "reason": reason}
            return
        arguments = {"description": game_studio_description(request)}
        step = self._run_tool(tools, "game_studio.create", arguments, len(steps) + 1, turn_id)
        step["source"] = "cerveau"
        steps.append(step)
        self._update(turn_id, steps=steps)
        from apex.assistant.game_studio import explain

        observation = step["observation"]
        output = observation.get("output") or {}
        error = observation.get("error")
        if error:
            record["explanation"] = explain(error.get("message"))
        meta["game_studio"] = {**record, "state": "launched" if step["ok"] else "refused",
                               "launched": step["ok"], "authorization": "lancement automatique permis",
                               "project_id": output.get("project_id"),
                               "mission_id": output.get("mission_id"), "status": output.get("status"),
                               "error": error}

    @staticmethod
    def _run_tool(tools: dict[str, Any] | None, name: str, arguments: dict[str, Any], number: int,
                  turn_id: str) -> dict[str, Any]:
        from apex.tools.model import ToolCall

        assert tools is not None
        if name not in tools["offered"]:
            observation = {"ok": False, "error": {"code": "not_offered",
                                                  "message": f"outil « {name} » non disponible dans ce tour"}}
            return {"step": number, "tool": name, "arguments": arguments, "ok": False, "observation": observation,
                    "duration_s": 0.0}
        if name == "game_studio.create" and tools.get("game_studio"):
            info = tools["game_studio"]
            if info.get("launches", 0) >= _GAME_STUDIO_LAUNCHES_PER_TURN:
                # Une mission coûte des minutes de GPU : un modèle qui boucle n'en lance jamais plusieurs.
                observation = {"ok": False, "error": {"code": "limit", "message": (
                    "une seule mission Game Studio par tour : suivez-la avec game_studio.status")}}
                return {"step": number, "tool": name, "arguments": arguments, "ok": False,
                        "observation": observation, "duration_s": 0.0}
            info["launches"] = info.get("launches", 0) + 1
            # Le calcul est le choix de l'utilisateur (Paramètres > Outils), jamais celui du modèle.
            arguments = {**arguments, "compute_backend": info["compute"]}
        result = tools["engine"].execute(ToolCall(tool=name, arguments=arguments, call_id=str(uuid.uuid4()),
                                                  requested_by=f"assistant:{turn_id}"), tools["context"])
        observation = {"ok": result.ok, "output": result.output,
                       "error": result.error.to_dict() if result.error else None}
        shown_args = {k: (v if not isinstance(v, str) or len(v) <= 600 else v[:600] + "…") for k, v in arguments.items()}
        return {"step": number, "tool": name, "arguments": shown_args, "ok": result.ok,
                "observation": json.loads(_clip(observation, 6000)) if len(_clip(observation, 6000)) < 6000
                else {"ok": result.ok, "summary": _clip(observation, 6000)},
                "duration_s": round(result.duration_s, 3)}

    def _update(self, turn_id: str, **fields: Any) -> None:
        from apex.db.engine import session_scope
        from apex.db.repositories import ConversationTurnRepository

        with session_scope(self._session_factory) as session:
            ConversationTurnRepository(session).update(turn_id, **fields)

    def _finish(self, turn_id: str, conversation_id: str, *, status: str, message_status: MessageStatus,
                content: str, error: str | None, meta: dict[str, Any], model_id: str | None = None,
                provider_id: str | None = None) -> Message:
        from apex.conversations.repository import ConversationRepository, MessageRepository

        message = MessageRepository(self._session_factory).create(
            conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=content, model_id=model_id,
            provider_id=provider_id, status=message_status, error=error, meta={**meta, "turn_id": turn_id})
        ConversationRepository(self._session_factory).touch(conversation_id)
        self._update(turn_id, status=status, assistant_message_id=message.id, error=error, finished_at=_now(),
                     steps=meta.get("steps", []))
        return message
