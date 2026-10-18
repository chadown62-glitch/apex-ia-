"""Le brief créatif d'une demande 3D (`apex.assistant.creative_brief`) : extraction déterministe, et ce que
Game Studio fera de chaque élément, sans rien promettre de plus que ce qu'il vérifie."""

from __future__ import annotations

import pytest

from apex.assistant.creative_brief import extract, support

RED_CAR = "Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender."
HYPERCAR = ("Crée une hypercar agressive photoréaliste jaune nacrée dans ce style, jantes noires forgées, "
            "étriers rouges, aileron en fibre de carbone, rendu showroom.")


def _status(brief: dict, aspect: str, asked: str) -> str:
    return next(i["status"] for i in brief["support"] if i["aspect"] == aspect and i["asked"] == asked)


def test_the_request_is_sorted_by_aspect() -> None:
    brief = extract(HYPERCAR)
    assert (brief["family"], brief["archetype"]) == ("vehicle", "hypercar")
    assert brief["styles"] == ["agressif"] and brief["finish"] == "photoréaliste"
    assert brief["setting"] == "showroom" and brief["materials"] == ["fibre de carbone"]
    assert brief["colors"] == [{"color": "jaune", "part": "carrosserie", "finish": "nacré"},
                               {"color": "noir", "part": "jantes", "finish": None},
                               {"color": "rouge", "part": "étriers", "finish": None}]
    assert "aileron arrière" in brief["features"] and "jantes forgées" in brief["features"]
    assert brief["reference_requested"] is True and brief["reference_images"] == 0
    assert extract(RED_CAR)["counts"] == {"roues": 4}


def test_only_what_game_studio_checks_is_called_verified() -> None:
    brief = extract(RED_CAR)
    checked = {"requirements": [{"id": "vehicle"}, {"id": "red"}]}  # ce que rend son extracteur
    items = {(i["aspect"], i["asked"]): i for i in support(brief, checked)}
    assert items[("objet", "voiture de sport")]["status"] == "verified"
    assert "même gabarit" in items[("objet", "voiture de sport")]["how"]
    red = items[("couleur", "rouge (carrosserie)")]
    assert red["status"] == "verified" and "pas sur la couleur perçue" in red["how"]
    assert items[("élément", "roues indépendantes")]["status"] == "verified"


def test_style_finish_colours_and_references_it_ignores_are_said_not_yet() -> None:
    brief = extract(HYPERCAR)
    brief["support"] = support(brief, {"requirements": [{"id": "vehicle"}]})
    assert _status(brief, "style", "agressif") == "not_yet"
    assert _status(brief, "rendu", "photoréaliste") == "not_yet"
    assert _status(brief, "couleur", "jaune nacré (carrosserie)") == "not_yet"
    # le rouge n'est vérifié que sur la caisse
    assert _status(brief, "couleur", "rouge (étriers)") == "not_yet"
    assert _status(brief, "élément", "aileron arrière") == "possible"
    assert _status(brief, "références", "style d'une image") == "not_yet"


def test_an_object_game_studio_does_not_see_in_the_words_is_refused_with_the_fix() -> None:
    brief = extract("Crée une hypercar agressive photoréaliste dans ce style.")
    item = support(brief, {"requirements": []})[0]
    assert item["status"] == "refused" and "« une voiture hypercar … »" in item["how"]
    character = extract("Modélise un personnage en armure")
    assert support(character, {"requirements": []})[0]["status"] == "refused"


def test_without_game_studio_nothing_is_called_verified() -> None:
    assert {i["status"] for i in support(extract(RED_CAR), None)} == {"unknown"}


def test_against_game_studios_real_extractor() -> None:
    """Mesuré avec SON extracteur : « hypercar » seul n'est pas une voiture pour lui (plan refusé),
    « une voiture hypercar » l'est."""
    pytest.importorskip("apex.game_studio.requirements", reason="Game Studio absent de cette installation")
    assert _status(extract("Crée une hypercar agressive photoréaliste."), "objet", "hypercar") == "refused"
    fixed = extract("Crée une voiture hypercar agressive rouge.")
    assert _status(fixed, "objet", "hypercar") == "verified"
    assert _status(fixed, "couleur", "rouge (carrosserie)") == "verified"
    assert _status(extract(RED_CAR), "objet", "voiture de sport") == "verified"
