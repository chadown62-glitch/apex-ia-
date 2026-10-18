"""Le brief créatif d'une demande 3D : ce que l'utilisateur a demandé, rangé par aspect, et ce que Game
Studio en fera.

Extraction déterministe, par un lexique français et anglais fixé ici (aucun modèle : reproductible et
testable) : famille et archétype, style, rendu visé, couleurs et finitions par partie, matériaux,
éléments de design, mise en scène, quantités, références d'images. Le texte de l'utilisateur reste
transmis tel quel à Game Studio ; le brief ne le remplace pas.

Pour chaque élément, `support` dit honnêtement ce qu'il deviendra aujourd'hui :

- `verified` : Game Studio le vérifie lui-même. Sa liste vient de SON extracteur de contraintes
  (`apex.game_studio.requirements.extract`, appelé, jamais modifié), complété des contrôles qualifiés connus
  (quatre roues indépendantes : précontrôle Game Engineer ; export .blend et .glb) ;
- `possible` : une opération bornée de Game Studio existe (aileron arrière : composant `rear_spoiler` de son
  catalogue de corrections), sans garantie qu'elle soit choisie ;
- `not_yet` : transmis dans la demande, mais aucun constructeur ni juge de Game Studio ne s'en sert encore ;
- `refused` : Game Studio refusera (aucun constructeur, ou objet qu'il ne reconnaît pas dans la demande) ;
- `unknown` : Game Studio absent de cette installation.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

BRIEF_VERSION = 1


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


# Chaque entrée : (valeur affichée, motif sur le texte normalisé sans accents). Le premier trouvé gagne.
ARCHETYPES = (
    ("hypercar", r"hyper[ -]?cars?|hypersportives?"),
    ("supercar", r"super[ -]?cars?|supersportives?"),
    ("voiture de sport", r"voitures? de sport|sportives?|sports? cars?|coupes? sport"),
    ("grand tourisme", r"\bgt\b|grand tourisme|grand tourer"),
    ("berline", r"berlines?|sedans?"),
    ("SUV", r"\bsuv\b|4x4|tout[ -]terrain"),
    ("pick-up", r"pick[ -]?ups?"),
    ("camion", r"camions?|trucks?"),
    ("char", r"\bchars?\b|tanks?"),
    ("voiture", r"voitures?|vehicules?|\bcars?\b|bolides?|automobiles?"),
    ("bâtiment", r"batiments?|buildings?|maisons?|garages?"),
    ("fauteuil", r"fauteuils?|chaises?|chairs?|armchairs?|sieges?"),
    ("arme", r"armes?|weapons?|fusils?|epees?|swords?|pistolets?"),
    ("personnage", r"personnages?|characters?|heros?|creatures?"),
)
FAMILY_OF = {
    "hypercar": "vehicle", "supercar": "vehicle", "voiture de sport": "vehicle",
    "grand tourisme": "vehicle",
    "berline": "vehicle", "SUV": "vehicle", "pick-up": "vehicle", "camion": "vehicle", "char": "vehicle",
    "voiture": "vehicle", "bâtiment": "building", "fauteuil": "chair", "arme": "weapon",
    "personnage": "character",
}
STYLES = (
    ("agressif", r"agressi\w*|aggressive|muscle\w*|bestial\w*|menacant\w*|feroce\w*|brutal\w*"),
    ("élégant", r"elegan\w*|raffine\w*|gracieu\w*"),
    ("futuriste", r"futuris\w*|concept[ -]?car|science[ -]fiction|sci[ -]fi|cyberpunk"),
    ("rétro", r"retro\w*|vintage|classique|classic|annees \d0"),
    ("épuré", r"epure\w*|minimalis\w*|sobre\w*|clean"),
    ("luxueux", r"lux\w*|premium|haut de gamme|prestig\w*"),
    ("course", r"course|racing|piste|track|circuit|endurance"),
)
FINISHES = (
    ("photoréaliste", r"photo[ -]?realis\w*|ultra[ -]?realis\w*|hyper[ -]?realis\w*"),
    ("réaliste", r"realis\w*|realistic"),
    ("stylisé", r"stylise\w*|stylized"),
    ("low-poly", r"low[ -]?poly"),
    ("cartoon", r"cartoon|anime|dessin anime"),
)
SETTINGS = (
    ("showroom", r"showroom|salon|exposition|concession"),
    ("studio", r"studio|fond (?:uni|neutre|blanc|noir)"),
    ("nuit", r"\bnuit\b|night|neons?"),
    ("extérieur", r"exterieur|outdoor|route|ville|city|montagne|desert|coucher de soleil|sunset"),
)
COLORS = (
    ("rouge", r"rouges?|\bred\b"), ("jaune", r"jaunes?|yellow"), ("orange", r"\boranges?\b"),
    ("vert", r"\bverte?s?\b|green"), ("bleu", r"bleue?s?|blue"), ("violet", r"violette?s?|purple"),
    ("rose", r"\broses?\b|pink"), ("blanc", r"blanche?s?|white"), ("noir", r"noire?s?|black"),
    ("gris", r"grise?s?|grey|gray"), ("argent", r"argente?e?s?|silver"), ("or", r"\bdore?e?s?\b|\bgold\b"),
)
COLOR_FINISHES = (
    ("mat", r"\bmate?s?\b|matte"), ("satiné", r"satine\w*|satin"), ("métallisé", r"metallis\w*|metallic"),
    ("nacré", r"nacre\w*|pearl\w*"), ("brillant", r"brillant\w*|glossy|vernis"), ("chromé", r"chrome\w*"),
)
PARTS = (
    ("jantes", r"jantes?|rims?|wheels?"), ("étriers", r"etriers?|calipers?"), ("toit", r"\btoit\b|roof"),
    ("intérieur", r"interieur|interior|sieges?|seats?|habitacle|cockpit"), ("vitres", r"vitres?|windows?"),
    ("carrosserie", r"carrosserie|caisse|\bbody\b|peinture|paint"),
)
MATERIALS = (
    ("fibre de carbone", r"carbone|carbon"), ("chrome", r"chrome\w*"), ("cuir", r"\bcuir\b|leather"),
    ("alcantara", r"alcantara"), ("aluminium brossé", r"alumini?um|\balu\b|brosse"),
    ("titane", r"titane|titanium"), ("bois", r"\bbois\b|wood\w*"), ("verre", r"\bverre\b|glass"),
)
FEATURES = (
    ("aileron arrière", r"aileron\w*|spoiler\w*|\bwing\b"),
    ("diffuseur", r"diffuseur\w*|diffuser\w*"),
    ("lame avant", r"lame avant|splitter\w*|becquet avant"),
    ("prises d'air", r"prises? d.air|entrees? d.air|ecopes?|ouies?|air intakes?|\bvents?\b|scoops?"),
    ("portes à ouverture verticale",
     r"portes? (?:papillon|elytre|en ciseaux|ciseaux)|butterfly doors?|scissor doors?|gull[ -]?wing"),
    ("feux à LED", r"\bleds?\b|phares?|feux|headlights?|tail ?lights?"),
    ("jantes forgées", r"\bforgee?s?\b|forged"),
    ("échappement", r"echappements?|exhausts?|silencieux"),
    ("toit vitré", r"toit (?:vitre|panoramique|ouvrant)|glass roof|sunroof"),
    ("roues indépendantes", r"roues? independantes?|independent wheels?"),
    ("export Blender", r"blender|\.blend|export\w*"),
)
NUMBERS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "huit": 8, "dix": 10,
           "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "eight": 8, "ten": 10}
COUNTED = re.compile(r"\b(\d+|" + "|".join(NUMBERS) + r")\s+(roues?|wheels?|portes?|doors?)\b")
REFERENCE_WORDS = (r"dans ce style|comme (?:sur |dans )?(?:l.|cette |ces )?(?:image|photo|reference)s?"
                   r"|d.apres (?:l.|cette |ces )?(?:image|photo)s?|references?|this style"
                   r"|like (?:the|this) (?:image|picture)")


def _find(table: tuple[tuple[str, str], ...], text: str, *, first: bool = False) -> list[str]:
    found = [label for label, pattern in table if re.search(r"(?:" + pattern + r")", text)]
    return found[:1] if first else found


def _colors(text: str) -> list[dict[str, Any]]:
    """Chaque couleur nommée, avec sa finition et la partie à laquelle elle se rapporte quand elles sont
    dans la même proposition (« jantes noires », « carrosserie jaune nacrée ») ; sinon la carrosserie."""
    out: list[dict[str, Any]] = []
    for clause in re.split(r"[,;.\n]| et | avec | and | with ", text):
        for label, pattern in COLORS:
            if not re.search(pattern, clause):
                continue
            part = _find(PARTS, clause, first=True)
            finish = _find(COLOR_FINISHES, clause, first=True)
            item = {"color": label, "part": part[0] if part else "carrosserie",
                    "finish": finish[0] if finish else None}
            if item not in out:
                out.append(item)
    return out


def _studio_requirements(request: str) -> dict[str, Any] | None:
    """Ce que Game Studio vérifiera lui-même : SON extracteur, appelé tel quel. None sans Game Studio."""
    try:
        from apex.game_studio.requirements import extract
    except ImportError:
        return None
    try:
        return extract({"description": request})
    except Exception:  # un extracteur qui refuse cette demande ne doit pas empêcher la proposition
        return None


def extract(request: str, *, reference_images: int = 0) -> dict[str, Any]:
    """Le brief de `request`, avec le sort de chaque élément dans Game Studio aujourd'hui."""
    text = _norm(request)
    archetype = _find(ARCHETYPES, text, first=True)
    counts = {}
    for number, noun in COUNTED.findall(text):
        key = "roues" if noun.startswith(("roue", "wheel")) else "portes"
        counts[key] = int(number) if number.isdigit() else NUMBERS[number]
    brief: dict[str, Any] = {
        "version": BRIEF_VERSION,
        "family": FAMILY_OF.get(archetype[0]) if archetype else None,
        "archetype": archetype[0] if archetype else None,
        "styles": _find(STYLES, text),
        "finish": (_find(FINISHES, text, first=True) or [None])[0],
        "setting": (_find(SETTINGS, text, first=True) or [None])[0],
        "colors": _colors(text),
        "materials": _find(MATERIALS, text),
        "features": _find(FEATURES, text),
        "counts": counts,
        "reference_images": reference_images,
        "reference_requested": bool(re.search(REFERENCE_WORDS, text)),
    }
    brief["support"] = support(brief, _studio_requirements(request))
    return brief


def _object(brief: dict[str, Any], studio: dict[str, Any] | None, checked: set[str]) -> tuple[str, str]:
    family, archetype = brief["family"], brief["archetype"]
    if family in ("weapon", "character"):
        return "refused", "Game Studio n'a pas de constructeur pour ce type d'objet"
    if studio is not None and family not in checked:
        # Mesuré : l'extracteur de Game Studio ne voit une voiture qu'à « voiture », « véhicule »,
        # « car »… ; un plan de voiture est alors refusé comme objet non demandé (`unrequested_object`).
        noun = {"vehicle": "une voiture", "building": "un bâtiment", "chair": "un fauteuil"}[family]
        return "refused", (f"Game Studio ne reconnaît pas « {archetype} » comme {noun} : son plan serait "
                           f"refusé. Écrivez « {noun} {archetype} … »")
    if family == "vehicle":
        if archetype == "voiture":
            return "verified", "une voiture, vérifiée par Game Studio ; gabarit unique de voiture"
        return "verified", ("une voiture, vérifiée par Game Studio ; son archétype (proportions, langage "
                            "de design) n'est pas encore modélisé : même gabarit pour toute voiture")
    return "verified", "type d'objet reconnu par Game Studio"


def support(brief: dict[str, Any], studio: dict[str, Any] | None) -> list[dict[str, str]]:
    """Le sort de chaque élément du brief dans Game Studio aujourd'hui (voir le docstring du module)."""
    checked = {item["id"] for item in (studio or {}).get("requirements", [])}
    items: list[dict[str, str]] = []

    def add(aspect: str, asked: str, status: str, how: str) -> None:
        items.append({"aspect": aspect, "asked": asked, "status": status, "how": how})

    if brief["archetype"]:
        add("objet", brief["archetype"], *_object(brief, studio, checked))
    for style in brief["styles"]:
        add("style", style, "not_yet", "transmis ; aucun constructeur ni juge ne mesure encore ce style")
    if brief["finish"]:
        add("rendu", brief["finish"], "not_yet",
            "transmis ; le rendu n'est pas encore réglable par la demande (éclairage et caméras fixes)")
    if brief["setting"]:
        add("mise en scène", brief["setting"], "not_yet", "transmis ; studio fixe de Game Studio")
    for color in brief["colors"]:
        label = color["color"] + (f" {color['finish']}" if color["finish"] else "") + f" ({color['part']})"
        key = {"rouge": "red", "bleu": "blue"}.get(color["color"])
        if color["part"] == "carrosserie" and key in checked:
            add("couleur", label, "verified",
                "vérifiée sur la valeur du shader de la carrosserie, pas sur la couleur perçue du rendu")
        else:
            add("couleur", label, "not_yet",
                "transmise ; Game Studio ne vérifie aujourd'hui que le rouge et le bleu de carrosserie")
    for material in brief["materials"]:
        if {"cuir": "leather", "bois": "wood"}.get(material) in checked:
            add("matériau", material, "verified", "demande vérifiée dans le plan de Game Studio")
        else:
            add("matériau", material, "not_yet", "transmis ; pas de matériau dédié")
    for feature in brief["features"]:
        if feature == "roues indépendantes":
            add("élément", feature, "verified", "quatre roues indépendantes : précontrôle Game Engineer")
        elif feature == "export Blender":
            add("élément", feature, "verified", "fichiers .blend et .glb livrés et vérifiés")
        elif feature == "aileron arrière" and brief["family"] == "vehicle":
            add("élément", feature, "possible",
                "composant borné du catalogue de corrections, sans garantie")
        else:
            add("élément", feature, "not_yet", "transmis ; aucun constructeur de cet élément")
    if brief["reference_images"] or brief["reference_requested"]:
        count = brief["reference_images"]
        asked = f"{count} image(s)" if count else "style d'une image"
        add("références", asked, "not_yet",
            "les images ne sont pas encore comprises par APEX : elles ne guident pas la création")
    if studio is None:  # sans Game Studio, rien n'est vérifié ni réalisé
        return [{**item, "status": "unknown", "how": "Game Studio absent de cette installation"}
                for item in items]
    return items
