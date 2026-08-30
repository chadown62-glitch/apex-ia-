"""Tests de apex.missions.dependencies — détection de cycle (étape 8).

Teste l'algorithme PUR (aucune DB) : étant donné un graphe déjà persisté
(mission_id -> depends_on) et les dépendances proposées pour une NOUVELLE
mission pas encore créée, `detect_cycle` doit trouver tout chemin qui
reboucle — y compris les exemples cités par la consigne étape 8
(A -> B -> A, A -> B -> C -> A) — sans jamais de faux positif sur un DAG
légitime.
"""

from __future__ import annotations

import unittest

from apex.missions.dependencies import detect_cycle


class TestDetectCycle(unittest.TestCase):
    def test_no_dependencies_is_never_a_cycle(self) -> None:
        self.assertIsNone(detect_cycle({}, new_deps=()))

    def test_depending_on_a_leaf_is_not_a_cycle(self) -> None:
        edges = {"A": ()}
        self.assertIsNone(detect_cycle(edges, new_deps=("A",)))

    def test_a_legitimate_chain_of_three_is_not_a_cycle(self) -> None:
        """A (aucune dépendance) <- B <- C : DAG légitime, jamais un faux positif."""
        edges = {"A": (), "B": ("A",), "C": ("B",)}
        self.assertIsNone(detect_cycle(edges, new_deps=("C",)))

    def test_diamond_shaped_dependencies_is_not_a_cycle(self) -> None:
        """B et C dépendent tous deux de A ; la nouvelle mission dépend des
        deux — un losange n'est pas un cycle."""
        edges = {"A": (), "B": ("A",), "C": ("A",)}
        self.assertIsNone(detect_cycle(edges, new_deps=("B", "C")))

    def test_direct_two_node_cycle_a_to_b_to_a_is_detected(self) -> None:
        """A -> B -> A, cité explicitement par la consigne étape 8."""
        edges = {"A": ("B",), "B": ("A",)}
        cycle = detect_cycle(edges, new_deps=("A",))
        self.assertIsNotNone(cycle)
        self.assertIn("A", cycle)
        self.assertIn("B", cycle)

    def test_three_node_cycle_a_to_b_to_c_to_a_is_detected(self) -> None:
        """A -> B -> C -> A, cité explicitement par la consigne étape 8."""
        edges = {"A": ("B",), "B": ("C",), "C": ("A",)}
        cycle = detect_cycle(edges, new_deps=("A",))
        self.assertIsNotNone(cycle)

    def test_cycle_reachable_only_transitively_is_still_detected(self) -> None:
        """Le cycle existant (A<->B) n'est pas dans les dépendances directes
        de la nouvelle mission, seulement atteint en descendant le graphe."""
        edges = {"A": ("B",), "B": ("A",), "Z": ("A",)}
        cycle = detect_cycle(edges, new_deps=("Z",))
        self.assertIsNotNone(cycle)

    def test_missing_dependency_ids_are_treated_as_leaves_not_cycles(self) -> None:
        """Une dépendance absente du graphe (id inconnu) n'est pas, en soi,
        un cycle — l'existence est vérifiée séparément par MissionService."""
        self.assertIsNone(detect_cycle({}, new_deps=("does-not-exist",)))


if __name__ == "__main__":
    unittest.main()
