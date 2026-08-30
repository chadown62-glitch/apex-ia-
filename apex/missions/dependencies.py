"""Vérification des dépendances entre missions — `Mission.depends_on`.

Le graphe de dépendances ne peut croître QUE par des feuilles : une mission
nouvellement créée peut dépendre de missions déjà existantes, mais aucune
mission existante ne peut ensuite être modifiée pour dépendre d'une mission
plus récente (`depends_on` est fixé à la création, jamais muté — aucune
méthode de ce dépôt ne le permet). Cette contrainte suffit en théorie à
garantir l'absence de cycle par construction : un nouveau nœud ne peut
jamais devenir la cible d'un arc déjà existant.

La vérification ci-dessous est une seconde ligne de défense EXPLICITE
(consigne étape 8 : « empêcher les cycles »), pas une confiance aveugle dans
cet invariant — en particulier utile si une évolution future introduisait
par erreur une mutation de `depends_on` sans relire ce raisonnement.
"""

from __future__ import annotations


class DependencyCycle(ValueError):
    """Un ensemble de dépendances proposé créerait un cycle."""


#: Identifiant qui ne peut jamais collisionner avec un vrai id de mission
#: (UUID) — représente la mission en cours de création, dont l'id définitif
#: n'est généré qu'APRÈS validation des dépendances (voir MissionService.create).
_PENDING = "__pending__"


def detect_cycle(
    edges: dict[str, tuple[str, ...]], *, new_deps: tuple[str, ...]
) -> tuple[str, ...] | None:
    """`edges` : dépendances déjà persistées (mission_id -> depends_on).

    Vérifie si une mission (pas encore créée) dépendant de `new_deps`
    créerait un cycle. Retourne le chemin du cycle trouvé (exploitable dans
    un message d'erreur), ou `None` si aucun cycle.
    """
    graph = dict(edges)
    graph[_PENDING] = new_deps

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> tuple[str, ...] | None:
        color[node] = GRAY
        path.append(node)
        for dep in graph.get(node, ()):
            state = color.get(dep, WHITE)
            if state == GRAY:
                cycle_start = path.index(dep)
                return tuple(path[cycle_start:]) + (dep,)
            if state == WHITE:
                found = visit(dep)
                if found is not None:
                    return found
        color[node] = BLACK
        path.pop()
        return None

    return visit(_PENDING)
