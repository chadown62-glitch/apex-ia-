"""CODE REPAIR derrière le contrat générique de capacité."""

from __future__ import annotations

import hashlib
from typing import Any

from apex.capabilities.code_repair.benchmark import CodeRepairBenchmark, suite_name
from apex.capabilities.code_repair.mutation import candidates
from apex.capabilities.code_repair.solver import METRIC, TaskTooLong, solve_suite, suite_report
from apex.capabilities.contract import Capability, CapabilityContract, CapabilityEvaluation
from apex.neural.dataset import TrainingCandidate
from apex.neural.model import ApexLM
from apex.neural.tokenizer import ByteBPETokenizer
from apex.sandbox.provider import SandboxProvider

ATTEMPTS = 3
CHILD_TIMEOUT_S = 1.0
#: Domaine du monde qui porte les solutions des tâches TRAIN (split train seulement).
TASKS_DOMAIN = "code_tasks"
TASKS_PREFIX = "task:code_repair/"

CONTRACT = CapabilityContract(
    name="code_repair",
    version="1.0",
    description=(
        "Réparer une fonction Python boguée : le modèle classe les correctifs candidats (éditions "
        "d'un jeton), les K premiers sont exécutés dans le sandbox contre des tests cachés, dans "
        "l'ordre du classement ; un échec fait passer au suivant."
    ),
    input="code source d'une fonction boguée + nom de la fonction + ENTRÉES de test (jamais les sorties)",
    output="correctif (source complète) choisi parmi les éditions d'un jeton, et son exécution",
    environment="sandbox NAMESPACED (namespaces, cgroups, privilège abaissé, réseau coupé), python3",
    allowed_tools=("classement par le modèle APEX", f"exécution sandbox de {ATTEMPTS} candidats au plus"),
    safety_constraints=(
        "aucune exécution hors sandbox ; sandbox indisponible -> évaluation refusée",
        "réseau coupé ; chaque candidat dans un processus enfant avec délai",
        "sorties attendues jamais transmises au sandbox ni au solveur",
        "espace de correction fermé : édition d'un jeton parmi des opérateurs déclarés",
        "exercices de sécurité DÉFENSIFS, fonctions pures de laboratoire, aucun système réel",
    ),
    success_criteria=(
        "un candidat exécuté passe TOUS les tests cachés (coût (rang-1)/K)",
        "acceptation d'un modèle : baisse significative du coût sur la suite held-out "
        "(bootstrap apparié par fonction) sans régression des suites protégées",
    ),
    failure_criteria=(
        f"aucun des {ATTEMPTS} candidats exécutés ne passe (coût 1)",
        "sortie fausse, exception, délai ou exécution invalide d'un candidat",
    ),
    reproducibility=(
        "benchmark gelé dans le monde (empreinte par suite et globale)",
        "classement déterministe (égalités départagées par l'ordre du générateur)",
        "PYTHONHASHSEED=0 dans le sandbox ; entrées littérales",
    ),
    provenance="corpus écrit pour APEX (code_repair/corpus.py) ; bugs injectés mécaniquement",
    metric=METRIC,
    guard_tolerance=0.05,
    target_value=0.25,
    training_domains=("code", TASKS_DOMAIN),
    acceptance_suite=suite_name("heldout"),
    suites=tuple(suite_name(s) for s in ("train", "validation", "heldout", "transfer")),
    memorization_suite=suite_name("train"),
    transfer_suites=(suite_name("transfer"),),
    limits=(
        "pas de génération libre : sélection parmi des éditions d'un jeton énumérées",
        "bugs à une seule édition ; pas de bug multi-lignes ni de refactoring",
        "le modèle ne voit pas les sorties des tests échoués pour choisir le candidat suivant",
    ),
)


class CodeRepairCapability(Capability):
    contract = CONTRACT

    def load(self, payload: dict[str, Any]) -> CodeRepairBenchmark:
        return CodeRepairBenchmark.from_dict(payload)

    def dump(self, data: CodeRepairBenchmark) -> dict[str, Any]:
        return data.to_dict()

    def fingerprint(self, data: CodeRepairBenchmark) -> str:
        return data.fingerprint

    def suite_fingerprints(self, data: CodeRepairBenchmark) -> dict[str, str]:
        return {name: suite.fingerprint for name, suite in data.suites.items()}

    def protected_texts(self, data: CodeRepairBenchmark) -> list[str]:
        """Références ET versions boguées des suites d'évaluation."""
        texts = list(data.eval_references())
        for suite in data.suites.values():
            if suite.split != "train":
                texts.extend(inst.buggy for inst in suite.instances)
        return texts

    def train_documents(self, data: CodeRepairBenchmark) -> list[TrainingCandidate]:
        docs = []
        for fid, source in sorted(data.train_references().items()):
            docs.append(
                TrainingCandidate(
                    candidate_id=f"{TASKS_PREFIX}{fid}",
                    text=source,
                    source_id=f"{TASKS_PREFIX}{fid}",
                    content_hash="sha256:" + hashlib.sha256(source.encode()).hexdigest(),
                    license_status="free",
                    quality="high_value",
                    origin="capability:code_repair:train",
                )
            )
        return docs

    def longest_input(self, data: CodeRepairBenchmark, tokenizer: ByteBPETokenizer) -> int:
        return max(
            len(tokenizer.encode(text, add_bos=True, add_eos=True))
            for suite in data.suites.values()
            for inst in suite.instances
            for text, _ in candidates(inst.buggy)
        )

    def evaluate(
        self,
        model: ApexLM,
        tokenizer: ByteBPETokenizer,
        data: CodeRepairBenchmark,
        *,
        provider: SandboxProvider,
    ) -> CapabilityEvaluation:
        reports = {}
        details = {}
        for name, suite in sorted(data.suites.items()):
            results = solve_suite(
                model, tokenizer, suite, provider, attempts=ATTEMPTS, child_timeout_s=CHILD_TIMEOUT_S
            )
            reports[name] = suite_report(suite, results, model)
            details[name] = [r.to_dict() for r in results]
        return CapabilityEvaluation(reports, details)


__all__ = ["CONTRACT", "CodeRepairCapability", "TaskTooLong"]
