"""Tests d'`apex.models.registry` — requêtes pures sur `ApexConfig`, aucune
DB, aucun réseau. Vérifie l'appariement `ModelRef` <-> `ModelEndpoint`
(même heuristique que `ApexConfig.validate()`, jamais réinventée) et les
filtres (capacité, localité, contexte, disponibilité).
"""

from __future__ import annotations

import copy
import unittest

from apex.config.loader import parse_config
from apex.models.registry import (
    filter_available,
    filter_by_capability_class,
    filter_by_min_context,
    filter_by_role,
    filter_local,
    resolve_candidates,
)

BASE: dict = {
    "version": 1,
    "phase": 1,
    "paths": {
        "data_root": "/home/apex/data", "workspaces": "/home/apex/data/workspaces",
        "artifacts": "/home/apex/data/artifacts", "logs": "/home/apex/data/logs",
    },
    "compute": {
        "pools": {
            "local-gpu": {
                "location": "local", "trust_level": "trusted",
                "capabilities": ["inference", "sandbox"],
                "gpus": [{"model": "RTX 4060 Ti", "vram_gb": 8}],
                "cpu_cores": 16, "ram_gb": 8,
            }
        }
    },
    "models": {
        "roles": {
            "coder_main": {
                "selection": "benchmark_driven",
                "candidates": [
                    {"family": "qwen2.5-coder", "version": "7b-instruct",
                     "quantization": "q4_k_m", "capability_class": "coder"},
                ],
            },
            "judge": {
                "candidates": [
                    {"family": "gemma4", "version": "12b",
                     "quantization": "q4_k_m", "capability_class": "judge"},
                ],
            },
            "unavailable_role": {
                "candidates": [
                    {"family": "phantom-model", "version": "1b",
                     "quantization": "q4_k_m", "capability_class": "fast"},
                ],
            },
        },
        "endpoints": [
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "qwen2.5-coder:7b-instruct", "max_context": 32768, "vram_gb": 5.0},
            {"pool_id": "local-gpu", "runtime": "ollama", "url": "http://127.0.0.1:11434",
             "served_model": "gemma4:12b-q4_K_M", "max_context": 16384, "vram_gb": 8.0},
        ],
    },
    "external": {"enabled": False, "allowed_contexts": ["interactive", "research"]},
    "budget": {"nightly_gpu_minutes": 600, "max_experiment_gpu_minutes": 240},
    "sandbox": {"provider": "docker", "network": "none"},
    "languages": {"python": {"wave": 1, "image": "python:3.12-slim", "oracle_strength": "strong"}},
    "services": {"postgres": {"host": "127.0.0.1", "port": 5432}},
}


def config(**overrides) -> dict:
    document = copy.deepcopy(BASE)
    for path, value in overrides.items():
        node = document
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return document


class TestResolveCandidates(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = parse_config(config())

    def test_one_candidate_per_declared_ref(self) -> None:
        candidates = resolve_candidates(self.cfg)
        # coder_main (1) + judge (1) + unavailable_role (1) = 3 refs déclarés
        self.assertEqual(len(candidates), 3)

    def test_a_ref_with_a_matching_endpoint_is_available(self) -> None:
        candidates = resolve_candidates(self.cfg)
        coder = filter_by_role(candidates, "coder_main")[0]
        self.assertTrue(coder.is_available)
        self.assertEqual(coder.endpoint.served_model, "qwen2.5-coder:7b-instruct")

    def test_a_ref_without_a_matching_endpoint_is_declared_but_unavailable(self) -> None:
        """Intention déclarée (ModelRef), aucun point de service — jamais
        confondu avec une capacité réellement joignable."""
        candidates = resolve_candidates(self.cfg)
        unavailable = filter_by_role(candidates, "unavailable_role")[0]
        self.assertFalse(unavailable.is_available)
        self.assertIsNone(unavailable.endpoint)

    def test_filter_available_excludes_declared_only_candidates(self) -> None:
        candidates = resolve_candidates(self.cfg)
        available = filter_available(candidates)
        self.assertEqual(len(available), 2)
        self.assertTrue(all(c.is_available for c in available))


class TestFilters(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = resolve_candidates(parse_config(config()))

    def test_filter_by_capability_class(self) -> None:
        coders = filter_by_capability_class(self.candidates, "coder")
        self.assertEqual(len(coders), 1)
        self.assertEqual(coders[0].ref.family, "qwen2.5-coder")

    def test_filter_local_excludes_nothing_when_none_are_external(self) -> None:
        local = filter_local(self.candidates, local=True)
        self.assertEqual(len(local), len(self.candidates))

    def test_filter_local_excludes_external_quantization(self) -> None:
        document = config()
        document["models"]["roles"]["cloud_role"] = {
            "candidates": [
                {"family": "gpt-5", "version": "1", "quantization": "external", "capability_class": "generalist"},
            ],
        }
        candidates = resolve_candidates(parse_config(document))
        local = filter_local(candidates, local=True)
        external = filter_local(candidates, local=False)
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0].ref.family, "gpt-5")
        self.assertNotIn(external[0], local)

    def test_filter_by_min_context_requires_a_real_endpoint(self) -> None:
        fits = filter_by_min_context(self.candidates, 20000)
        self.assertEqual(len(fits), 1)  # seul coder_main (32768) tient, judge (16384) exclu
        self.assertEqual(fits[0].ref.family, "qwen2.5-coder")

    def test_filter_by_min_context_excludes_candidates_without_endpoint(self) -> None:
        fits = filter_by_min_context(self.candidates, 1)
        self.assertTrue(all(c.endpoint is not None for c in fits))
        self.assertEqual(len(fits), 2)  # les 2 avec endpoint, pas unavailable_role


class TestModelCandidateIsLocal(unittest.TestCase):
    def test_local_quantization_is_local(self) -> None:
        candidates = resolve_candidates(parse_config(config()))
        self.assertTrue(all(c.is_local for c in candidates))


if __name__ == "__main__":
    unittest.main()
