"""Les cinq modes de travail de l'assistant. Un mode change RÉELLEMENT le comportement : les consignes
données au modèle (rôle `system`), les outils qui lui sont offerts (et donc les permissions accordées au moteur
d'outils) et la vue que l'interface ajoute à la conversation. `limits` dit ce que le mode ne fait pas : l'interface
l'affiche, et rien n'est jamais présenté comme disponible sans l'être."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from apex.tools.model import Permission

_COMMON = (
    "Tu es APEX, un assistant qui fonctionne entièrement sur l'ordinateur de l'utilisateur, sans accès à "
    "Internet. Réponds dans la langue de l'utilisateur (le français par défaut), de façon claire et structurée "
    "(titres courts, listes, blocs de code avec leur langage). N'invente jamais de faits, de sources, de chiffres "
    "ni de résultats d'exécution : si tu ne sais pas ou si tu n'as pas pu vérifier, dis-le."
)


#: Permissions des outils de Game Studio (`apex.tools.model`, ajoutées avec Game Studio) : absentes d'une
#: installation sans Game Studio, et alors rien n'est demandé. Lancer ou annuler une mission n'est en plus
#: accordé que si l'utilisateur l'a permis (Paramètres > Outils > Missions Game Studio).
def _permissions(*names: str) -> frozenset[Permission]:
    return frozenset(p for name in names if (p := getattr(Permission, name, None)) is not None)


GAME_STUDIO_LAUNCH = _permissions("GAME_STUDIO_CREATE", "GAME_STUDIO_CANCEL")
GAME_STUDIO_PERMISSIONS = GAME_STUDIO_LAUNCH | _permissions("GAME_STUDIO_READ")


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    summary: str
    instructions: str
    #: Outils offerts au modèle dans ce mode (sous réserve des permissions accordées par les paramètres).
    tools: tuple[str, ...] = ()
    #: Permissions demandées par ce mode ; les paramètres peuvent en retirer, jamais en ajouter.
    permissions: frozenset[Permission] = field(default_factory=frozenset)
    #: Vue ajoutée par l'interface : `chat` (aucune), `code` (explorateur de l'espace de travail),
    #: `design` (palettes et contrastes de la réponse), `finance` (tableaux de projection des outils),
    #: `studio3d` (aperçus et fichiers des modèles 3D).
    view: str = "chat"
    limits: tuple[str, ...] = ()

    def describe(self, available: Collection[str] | None = None) -> dict[str, Any]:
        """`available` : outils réellement enregistrés dans cette installation ; les autres ne sont pas
        annoncés (Game Studio absent : rien n'est présenté comme disponible)."""
        tools = [t for t in self.tools if available is None or t in available]
        return {"id": self.id, "label": self.label, "summary": self.summary, "tools": tools,
                "view": self.view, "limits": list(self.limits)}


MODES: dict[str, Mode] = {
    "normal": Mode(
        id="normal", label="Normal",
        summary="Conversation générale : questions, apprentissage, rédaction, raisonnement.",
        instructions=_COMMON + (
            " Pour une question d'apprentissage, explique pas à pas avec un exemple. Pour une rédaction, propose "
            "un texte prêt à l'emploi puis, brièvement, ce qui peut être ajusté. Pour un raisonnement, expose les "
            "étapes et signale les hypothèses."),
        limits=("Pas d'accès à Internet ni aux fichiers de l'ordinateur.",),
    ),
    "code": Mode(
        id="code", label="Code",
        summary="Programmation : analyser, écrire et modifier des fichiers, exécuter et tester dans la sandbox.",
        instructions=_COMMON + (
            " Mode Code : tu es un ingénieur logiciel rigoureux. Tu travailles dans l'espace de travail de la "
            "conversation (ou du projet), qui contient les fichiers listés par les outils et les pièces jointes "
            "du projet (dossier pieces-jointes). Lis un fichier avant de le modifier ; dans un gros projet, "
            "cherche avec fs.search au lieu de tout lire, et modifie un passage avec fs.edit au lieu de "
            "réécrire le fichier entier (fs.write pour un fichier nouveau ou court). Exécute ou teste ce que tu écris quand c'est possible (sandbox isolée, sans réseau). "
            "N'affirme jamais qu'un code fonctionne sans l'avoir exécuté avec un outil : sinon, dis qu'il n'a pas "
            "été exécuté. Dans ta réponse finale, résume ce que tu as modifié et ce qui a été vérifié."),
        tools=("fs.list", "fs.read", "fs.search", "project.inspect", "fs.write", "fs.edit", "code.run", "tests.run",
               "model3d.create"),
        permissions=frozenset({Permission.READ_WORKSPACE, Permission.WRITE_WORKSPACE, Permission.EXECUTE_SANDBOX}),
        view="code",
        limits=("Exécution limitée à Python, dans la sandbox (réseau coupé, temps et mémoire bornés).",
                "Accès limité à l'espace de travail : jamais aux autres fichiers de l'ordinateur."),
    ),
    "design": Mode(
        id="design", label="Design",
        summary="Conception d'interfaces et direction artistique : palettes, typographie, mise en page, CSS.",
        instructions=_COMMON + (
            " Mode Design : tu es directeur artistique et designer d'interfaces. Tu proposes une direction "
            "artistique argumentée, des palettes (codes hexadécimaux #RRGGBB), des typographies, une hiérarchie, "
            "une grille, des composants et leurs états, et du CSS quand c'est utile. Vérifie le contraste de "
            "chaque couple texte/fond avec l'outil color.contrast et cite le ratio obtenu. Tu ne génères pas "
            "d'images et tu ne vois pas d'images : décris les références visuelles avec des mots précis."),
        tools=("color.contrast",),
        view="design",
        limits=("Pas de génération ni d'analyse d'images.",
                "Les palettes citées dans la réponse sont affichées en nuancier, contrastes calculés par APEX."),
    ),
    "finance": Mode(
        id="finance", label="Finance",
        summary="Budgets, dépenses, projections, comparaison de scénarios, objectifs financiers.",
        instructions=_COMMON + (
            " Mode Finance : tu aides à construire des budgets, analyser des dépenses, projeter une épargne, "
            "comparer des scénarios et planifier des objectifs. Montre chaque calcul explicitement (formule, "
            "valeurs, résultat). Pour tout calcul non trivial, utilise les outils (calc.evaluate, "
            "finance.projection, finance.loan) au lieu de calculer de tête, et reprends leurs résultats exacts. "
            "Énonce les hypothèses (taux, durée, inflation, fiscalité). Tu n'as pas accès aux marchés ni aux "
            "comptes de l'utilisateur ; ne présente jamais une projection comme une certitude ni comme un conseil "
            "d'investissement personnalisé."),
        tools=("calc.evaluate", "finance.projection", "finance.loan"),
        view="finance",
        limits=("Pas d'accès aux comptes bancaires ni aux cours en temps réel.",
                "Projections déterministes à taux constant : ce sont des scénarios, pas des prévisions."),
    ),
    "3d": Mode(
        id="3d", label="3D",
        summary="Modélisation 3D : véhicules, armes, personnages ; fichiers pour Blender, Unity et Unreal.",
        instructions=_COMMON + (
            " Mode 3D : tu conçois des modèles 3D pour le jeu vidéo et la visualisation. Pour CHAQUE modèle demandé, "
            "appelle l'outil model3d.create (generator : vehicle, weapon ou character ; kind : car, truck, tank, "
            "motorbike, sword, axe, rifle, pistol ou humanoid ; dimensions en mètres ; build : slim, normal ou "
            "strong ; color : un nom ou #RRGGBB) : il construit une géométrie exacte et écrit les fichiers. Ne décris "
            "jamais une géométrie que l'outil n'a pas construite. Dans ta réponse, reprends le résumé de l'outil "
            "(dimensions, pièces, triangles) et cite les fichiers produits : .glb (Blender, Unreal), .obj (Unity), "
            "script Blender, script Unity C#, classe Unreal C++. Tu peux lire et adapter ces scripts avec fs.read "
            "et fs.write. Dis clairement que ce sont des modèles low-poly de base, et que les scripts Unity et "
            "Unreal n'ont été vérifiés que pour la syntaxe."),
        tools=("model3d.create", "game_studio.create", "game_studio.status", "game_studio.results",
               "game_studio.cancel", "fs.list", "fs.read", "fs.search", "fs.write", "fs.edit"),
        permissions=(frozenset({Permission.READ_WORKSPACE, Permission.WRITE_WORKSPACE})
                     | GAME_STUDIO_PERMISSIONS),
        view="studio3d",
        limits=("Modèles paramétriques low-poly (véhicule, arme, personnage), pas de sculpture ni de textures.",
                "Scripts Unity et Unreal de départ : syntaxe vérifiée, non exécutés dans les moteurs.",
                "Missions Game Studio (si installé) : lancées seulement si permis (Paramètres > Outils).",
                "Accès limité à l'espace de travail de la conversation ou du projet."),
    ),
}

DEFAULT_MODE = "normal"


def get_mode(mode_id: str | None) -> Mode:
    """Mode d'une conversation ; une valeur absente (conversation antérieure) est `normal`."""
    if mode_id is None or mode_id == "":
        return MODES[DEFAULT_MODE]
    if mode_id not in MODES:
        raise ValueError(f"mode « {mode_id} » inconnu ({', '.join(MODES)})")
    return MODES[mode_id]
