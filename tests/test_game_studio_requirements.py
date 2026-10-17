"""Held-out rule failures: count, substitution, unsupported features, no artistic claims."""

import pytest

from apex.game_studio.planning import validate_plan
from apex.game_studio.requirements import extract, judge
from tests.test_game_studio_planning import CAP, REQ


def task(key, kind, text, deps=()):
    return {"key": key, "kind": kind, "description": text, "depends_on": list(deps)}


def test_two_chairs_are_not_one_and_gallery_is_not_garage():
    request = {**REQ, "description": "Une galerie avec deux fauteuils bleus."}
    report = judge(
        extract(request),
        [task("room", "building", "Un garage moderne"), task("seat", "chair", "Un fauteuil bleu")],
    )
    assert {d["defect"] for d in report["defects"]} == {"quantity_mismatch", "omission_or_substitution"}
    assert not report["artistic_fidelity_certified"]


def test_matching_specialized_name_does_not_qualify_generic_builder():
    request = {**REQ, "description": "Une pharmacie de quartier avec quatre rendus."}
    plan = validate_plan(
        {"steps": [task("shop", "building", "Une pharmacie de quartier")], "unmet_requirements": []},
        request=request,
        capabilities=CAP,
    )
    assert plan["coverage"]["verdict"] == "pass_in_declared_scope"
    assert plan["steps"][0]["status"] == "not_implemented"
    assert not plan["fully_feasible"]


def test_three_independent_assets_cover_explicit_quantity():
    contract = extract({"description": "Create three blue armchairs with leather cushions."})
    report = judge(contract, [task(str(i), "chair", "Blue leather armchair") for i in range(3)])
    assert report["verdict"] == "pass_in_declared_scope"
    assert report["unverified"]  # scope is never universal


def test_unknown_tools_must_be_disclosed_and_contradictions_never_pass():
    contract = extract({"description": "Un garage avec des outils, sans voiture."})
    report = judge(
        contract, [task("garage", "building", "Un garage"), task("car", "vehicle", "Une voiture")]
    )
    assert {d["defect"] for d in report["defects"]} >= {
        "unsupported_object_not_disclosed",
        "ambiguous_or_contradictory_entity_request",
    }


def test_missing_quantity_is_rejected_before_execution():
    with pytest.raises(ValueError, match="requirement_coverage_failed"):
        validate_plan(
            {"steps": [task("chair", "chair", "Fauteuils modernes")], "unmet_requirements": []},
            request={**REQ, "description": "Trois fauteuils modernes."},
            capabilities=CAP,
        )


def test_paint_judge_measures_shader_channels_not_beauty():
    from apex.game_studio.requirements import material_defects

    contract = extract({"description": "Une voiture bleue avec quatre rendus."})

    def hierarchy(color):
        return {"chassis": {"pbr": {"paint": {"Base Color": {"value": color, "linked": False}}}}}

    assert material_defects(contract, hierarchy([0.018, 0.105, 0.145, 1]), "vehicle")
    assert not material_defects(contract, hierarchy([0.02, 0.06, 0.5, 1]), "vehicle")
    assert material_defects(contract, hierarchy([0.5, 0.02, 0.02, 1]), "vehicle")


def test_courtyard_ground_is_selected_from_request():
    from apex.game_studio.requirements import scene_setting

    assert scene_setting({"description": "Une cour avec deux fauteuils"}) == "courtyard"
    assert scene_setting({"description": "A courtyard with a building"}) == "courtyard"
    assert scene_setting({"description": "Un bâtiment de rue"}) == "street"
