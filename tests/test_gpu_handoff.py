"""Partage du GPU entre les modèles principaux et les spécialistes (`apex.models.gpu_handoff`), côté
Cerveau.

Sans GPU dans cet environnement : les tests prouvent le protocole (bail exclusif, attente des générations
en cours, accusé de réception, admissions, reprise) avec des modèles simulés sur « CUDA », et le passage du
vrai Cerveau par ce protocole sur le processeur. Aucune mesure CUDA réelle n'est revendiquée ici.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from apex.models import gpu_handoff
from apex.models.gpu_handoff import GpuGovernor, Resident, SharedLease, TransitionTimeout

#: Le verrou exact de Game Studio (`apex.game_graphics.resources._locked`), recopié pour le prouver dans un
#: AUTRE processus quand Game Studio n'est pas installé.
_CODEX_LOCK = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    path = Path(sys.argv[1])
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    handle.write(b"0"); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("refused"); sys.exit(0)
        print("acquired")
""")


def _other_process_can_lock(path: Path) -> bool:
    done = subprocess.run([sys.executable, "-c", _CODEX_LOCK, str(path)], capture_output=True, text=True,
                          timeout=30, check=True)
    return done.stdout.strip() == "acquired"


class _FakeCuda:
    """Un modèle principal simulé sur CUDA (verrou de génération, déchargement enregistré)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.loaded = True
        self.unloads = 0

    def residents(self) -> list[Resident]:
        return [Resident("apex_brain:test", self.lock, self.unload)] if self.loaded else []

    def unload(self) -> None:
        self.loaded = False
        self.unloads += 1


def _governor(tmp_path: Path, *, cuda: bool = True, memory=None, profile: bool = True) -> GpuGovernor:
    lease = tmp_path / ".apex" / "model-session.lock"
    return GpuGovernor(lease_path=lambda: lease if profile else None, cuda_available=lambda: cuda,
                       cuda_memory=memory or (lambda: None))


def test_the_lease_is_exclusive_in_this_process_and_against_another_one(tmp_path) -> None:
    path = tmp_path / "model-session.lock"
    first, second = SharedLease(path), SharedLease(path)
    assert first.try_acquire() and first.held
    assert not second.try_acquire()
    assert not _other_process_can_lock(path)  # même primitive que Game Studio, autre processus : refusé
    first.release()
    assert _other_process_can_lock(path) and second.try_acquire()
    second.release()


def test_cuda_is_used_only_with_the_lease_and_never_while_someone_else_holds_it(tmp_path) -> None:
    assert _governor(tmp_path, cuda=False).place("auto") == ("cpu", "aucun GPU CUDA")
    assert _governor(tmp_path).place("cpu") == ("cpu", "processeur demandé")
    governor = _governor(tmp_path)
    device, reason = governor.place("auto")
    assert device == "cuda" and "bail GPU tenu" in reason and governor.status()["lease_held"]
    assert not _other_process_can_lock(tmp_path / ".apex" / "model-session.lock")
    other = _governor(tmp_path)  # un autre détenteur (un spécialiste, par exemple) : le bail est pris
    assert other.place("auto") == ("cpu", "GPU tenu par un autre travail (bail model-session.lock)")
    device, reason = _governor(tmp_path, profile=False).place("auto")
    assert device == "cuda" and "sans profil APEX" in reason


def test_request_gpu_waits_for_the_running_generation_then_unloads_and_hands_over(tmp_path) -> None:
    memory = iter([{"allocated": 900, "reserved": 1200, "device_free": 100, "device_total": 8000},
                   {"allocated": 0, "reserved": 0, "device_free": 7900, "device_total": 8000}])
    governor = _governor(tmp_path, memory=lambda: next(memory))
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    assert governor.place("auto")[0] == "cuda"
    running = threading.Event()
    finished = []

    def generation() -> None:
        with governor.generation():
            running.set()
            time.sleep(0.4)  # une génération en cours n'est jamais interrompue
            finished.append(model.loaded)

    worker = threading.Thread(target=generation)
    worker.start()
    running.wait(5)
    ack = governor.request_gpu(10, requester="game_studio:mission-1")
    worker.join()
    assert finished == [True] and model.unloads == 1  # déchargé APRÈS la fin de la génération
    assert ack["released"] is True and ack["waited_s"] >= 0.3 and ack["unloaded"] == ["apex_brain:test"]
    assert ack["lease_held"] is False and ack["admissions_open"] is False
    assert ack["cuda"]["measured"] is True and ack["cuda"]["freed_allocated"] == 900
    # Le spécialiste peut maintenant prendre le bail.
    assert _other_process_can_lock(tmp_path / ".apex" / "model-session.lock")
    # Pendant le prêt : le Cerveau répond sur le processeur, et le dit.
    assert governor.place("auto") == ("cpu", "GPU prêté à un spécialiste (game_studio:mission-1)")
    assert governor.return_gpu(requester="game_studio:mission-1") == {
        "returned": True, "resumed": True, "requester": "game_studio:mission-1",
        "lent_to": "game_studio:mission-1", "lenders": {}, "admissions_open": True,
        "protocol_version": gpu_handoff.PROTOCOL_VERSION}
    assert governor.place("auto")[0] == "cuda"  # reprise : le bail est repris


def test_a_generation_longer_than_the_delay_is_never_cut_and_nothing_is_released(tmp_path) -> None:
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    governor.place("auto")
    release = threading.Event()
    running = threading.Event()

    def generation() -> None:
        with governor.generation():
            running.set()
            release.wait(5)

    worker = threading.Thread(target=generation)
    worker.start()
    running.wait(5)
    ack = governor.request_gpu(0.2, requester="game_studio:mission-2")
    assert ack["released"] is False and "au-delà du délai" in ack["reason"]
    assert model.unloads == 0 and ack["lease_held"] is True and ack["admissions_open"] is True
    release.set()
    worker.join()
    assert not _other_process_can_lock(tmp_path / ".apex" / "model-session.lock")  # le spécialiste attend


def test_new_generations_wait_for_the_transition_and_time_out_within_their_own_delay(tmp_path) -> None:
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    governor.place("auto")
    running = threading.Event()
    release = threading.Event()

    def generation() -> None:
        with governor.generation():
            running.set()
            release.wait(5)

    worker = threading.Thread(target=generation)
    worker.start()
    running.wait(5)
    acks: list[dict] = []
    quiesce = threading.Thread(target=lambda: acks.append(governor.request_gpu(5, requester="gs")))
    quiesce.start()
    time.sleep(0.1)
    # Admise pendant la transition : elle attend, dans son propre délai.
    with pytest.raises(TransitionTimeout), governor.generation(deadline=time.monotonic() + 0.2):
        pass
    assert governor.request_gpu(1, requester="autre")["reason"] == "une transition est déjà en cours"
    release.set()
    worker.join()
    quiesce.join()
    assert acks[0]["released"] is True
    # Après la transition : admise, sur le processeur.
    with governor.generation(deadline=time.monotonic() + 1):
        assert governor.place("auto")[0] == "cpu"


def test_a_busy_model_lock_after_the_wait_is_reported_never_forced(tmp_path) -> None:
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    governor.place("auto")
    model.lock.acquire()  # occupé hors des générations comptées
    try:
        ack = governor.request_gpu(0.2, requester="gs")
    finally:
        model.lock.release()
    assert ack["released"] is False and "encore occupé" in ack["reason"] and model.unloads == 0


def test_the_lease_is_returned_when_the_last_cuda_model_is_unloaded(tmp_path) -> None:
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    governor.place("auto")
    governor.unloaded()
    assert governor.status()["lease_held"] is True  # encore un modèle sur CUDA
    model.unload()
    governor.unloaded()
    assert governor.status()["lease_held"] is False and _other_process_can_lock(
        tmp_path / ".apex" / "model-session.lock")


def test_without_cuda_the_ack_says_nothing_was_measured(tmp_path) -> None:
    ack = _governor(tmp_path, cuda=False).request_gpu(1, requester="gs")
    assert ack["released"] is True and ack["unloaded"] == []
    assert ack["cuda"] == {"measured": False, "reason": "aucun GPU CUDA dans ce processus"}


def test_the_real_brain_generates_through_the_protocol_and_says_where_it_ran(tmp_path, monkeypatch) -> None:
    import torch

    from apex.assistant import engines
    from apex.brain.artifact import export
    from apex.brain.model import ApexLM
    from apex.brain.tokenizer import CHAT_SPECIAL_TOKENS, ByteBPETokenizer
    from apex.models import apex_brain
    from apex.models.model import GenerationRequest
    from apex.models.provider import get_default_provider
    from tests.test_brain_v2 import TEXTS, _tiny  # le même Cerveau minuscule que ses propres tests

    tokenizer = ByteBPETokenizer.train(TEXTS, 600, special_tokens=CHAT_SPECIAL_TOKENS, pattern="v2")
    torch.manual_seed(3)
    export(ApexLM(_tiny(tokenizer)).eval(), tokenizer, tmp_path / "apex-brain-v2", {"name": "test"},
           quantization="apex-q5")
    monkeypatch.setenv("APEX_BRAIN_DIR", str(tmp_path))
    monkeypatch.setattr(gpu_handoff, "_GOVERNOR", _governor(tmp_path, cuda=False))
    apex_brain.unload_all()
    candidate = engines.brain_candidate()
    provider = get_default_provider(candidate.endpoint)
    request = GenerationRequest(endpoint=candidate.endpoint, prompt="x", system="Tu es APEX.", timeout_s=30,
                                max_tokens=4, messages=(("user", "Bonjour"),))
    result = provider.generate(request)
    assert result.facts["device"] == "cpu" and result.facts["device_reason"] == "aucun GPU CUDA"
    ack = gpu_handoff.request_gpu(2, requester="game_studio:test")
    assert ack["released"] is True and ack["unloaded"] == []  # rien sur CUDA ici : rien à décharger
    assert provider.generate(request).facts["device"] == "cpu"  # le Cerveau continue de répondre
    assert gpu_handoff.return_gpu(requester="game_studio:test")["resumed"] is True
    apex_brain.unload_all()


def test_the_brains_lease_and_game_studios_real_lease_exclude_each_other(tmp_path, monkeypatch) -> None:
    """Avec le vrai `resource_lease` de Game Studio (ignoré sans Game Studio) : jamais les deux à la fois,
    et le spécialiste n'obtient le GPU qu'après l'accusé de réception du Cerveau."""
    resources = pytest.importorskip("apex.game_graphics.resources", reason="Game Studio absent")
    monkeypatch.setenv("APEX_HOME", str(tmp_path))
    governor = GpuGovernor(cuda_available=lambda: True, cuda_memory=lambda: None)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    assert governor.place("auto")[0] == "cuda"  # le Cerveau tient le bail partagé
    with pytest.raises(OSError), resources.resource_lease(tmp_path / "runtime" / "resource.lock"):
        pass
    ack = governor.request_gpu(2, requester="game_studio:test")
    assert ack["released"] is True and model.unloads == 1
    with resources.resource_lease(tmp_path / "runtime" / "resource.lock"):  # le spécialiste l'obtient
        assert governor.place("auto")[0] == "cpu"
        governor.return_gpu(requester="game_studio:test")
        assert governor.place("auto") == ("cpu", "GPU tenu par un autre travail (bail model-session.lock)")
    assert governor.place("auto")[0] == "cuda"  # rendu par le spécialiste : repris par le Cerveau


def test_loans_are_counted_and_admissions_reopen_only_at_the_last_return(tmp_path) -> None:
    """Protocole 2 (demandé par Codex, Issue #21) : le retour d'un prêt ne rouvre jamais les admissions
    tant qu'un autre court ; un retour inconnu ne change rien ; `lenders` se lit sans analyser `lent_to`."""
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    assert governor.place("auto")[0] == "cuda"
    mission = governor.request_gpu(1, requester="game_studio:launch-1")
    assert mission["released"] is True and model.unloads == 1
    assert mission["lenders"] == {"game_studio:launch-1": 1} and mission["protocol_version"] == 2
    step = governor.request_gpu(1, requester="game_studio:step")
    assert step["released"] is True and step["unloaded"] == [] and "déjà prêté" in step["reason"]
    assert step["lenders"] == {"game_studio:launch-1": 1, "game_studio:step": 1}
    assert governor.return_gpu(requester="game_studio:step") == {
        "returned": True, "resumed": False, "requester": "game_studio:step",
        "lent_to": "game_studio:launch-1, game_studio:step", "lenders": {"game_studio:launch-1": 1},
        "admissions_open": False, "protocol_version": 2,
        "reason": "GPU encore prêté (game_studio:launch-1)"}
    assert governor.place("auto") == ("cpu", "GPU prêté à un spécialiste (game_studio:launch-1)")
    before = governor.status()
    unknown = governor.return_gpu(requester="inconnu")
    assert unknown["returned"] is False and unknown["resumed"] is False
    assert unknown["reason"] == "aucun prêt à ce demandeur"
    assert governor.status() == before  # rien n'a changé
    assert governor.return_gpu(requester="game_studio:step")["returned"] is False  # déjà rendu
    back = governor.return_gpu(requester="game_studio:launch-1")
    assert back["returned"] is True and back["resumed"] is True and back["lenders"] == {}
    assert governor.status()["lent_to"] is None and governor.place("auto")[0] == "cuda"


def test_the_same_requester_counts_each_accepted_request_only(tmp_path) -> None:
    governor = _governor(tmp_path)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    governor.place("auto")
    model.lock.acquire()  # refus : jamais compté
    try:
        assert governor.request_gpu(0.1, requester="gs")["released"] is False
    finally:
        model.lock.release()
    assert governor.status()["lenders"] == {}
    governor.request_gpu(1, requester="gs")
    governor.request_gpu(1, requester="gs")
    assert governor.status()["lenders"] == {"gs": 2}
    assert governor.return_gpu(requester="gs")["resumed"] is False
    assert governor.return_gpu(requester="gs")["resumed"] is True


def test_codexs_real_adapter_borrows_and_returns_through_this_governor(tmp_path, monkeypatch) -> None:
    """Avec le vrai adaptateur de Game Studio (`apex.game_studio.gpu`, Codex #29 ; ignoré sans lui) et ce
    vrai gouverneur : Cerveau sur un « CUDA » simulé, accusé accepté, bail obtenu, retour accepté, puis le
    Cerveau reprend le GPU. Aucune mesure CUDA réelle."""
    adapter = pytest.importorskip("apex.game_studio.gpu", reason="adaptateur GPU de Game Studio absent")
    monkeypatch.setenv("APEX_HOME", str(tmp_path))
    governor = GpuGovernor(cuda_available=lambda: True, cuda_memory=lambda: None)
    model = _FakeCuda()
    governor.register("apex_brain", model.residents)
    monkeypatch.setattr(gpu_handoff, "_GOVERNOR", governor)
    monkeypatch.setattr(adapter, "check_external_workloads", lambda: None)
    import apex.game_graphics.resources as resources

    monkeypatch.setattr(resources, "check_external_workloads", lambda: None)
    assert governor.place("auto")[0] == "cuda"
    with adapter.resource_lease(tmp_path / "mission" / "resource.lock"):
        assert model.unloads == 1 and governor.place("auto")[0] == "cpu"
        assert list(governor.status()["lenders"].values()) == [1]
    assert governor.status()["lenders"] == {} and governor.status()["admissions_open"] is True
    assert governor.place("auto")[0] == "cuda"


def test_with_nothing_on_cuda_a_loan_never_waits_for_processor_generations(tmp_path) -> None:
    """Un Cerveau qui répond sur le processeur ne tient pas le bail : la mission n'attend pas sa réponse,
    et toute génération suivante reste sur le processeur pendant le prêt."""
    governor = _governor(tmp_path)
    assert governor.place("cpu")[0] == "cpu"
    running, release = threading.Event(), threading.Event()

    def generation() -> None:
        with governor.generation():
            running.set()
            release.wait(5)

    worker = threading.Thread(target=generation)
    worker.start()
    running.wait(5)
    try:
        ack = governor.request_gpu(0.2, requester="game_studio:launch-2")
    finally:
        release.set()
        worker.join()
    assert ack["released"] is True and ack["reason"] == "aucun modèle principal sur CUDA"
    assert ack["waited_s"] < 0.2 and ack["admissions_open"] is False
    (tmp_path / ".apex").mkdir()
    assert _other_process_can_lock(tmp_path / ".apex" / "model-session.lock")
    assert governor.place("auto") == ("cpu", "GPU prêté à un spécialiste (game_studio:launch-2)")
    assert governor.return_gpu(requester="game_studio:launch-2")["resumed"] is True
