"""L'instrument de mesure des rendus (`apex.assistant.render_metrics`), sur des images construites ici
dont on connaît la vérité : couleur, part de l'image, séparation du fond, exposition."""

from __future__ import annotations

import io

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from apex.assistant import render_metrics  # noqa: E402


def _png(array) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array.astype("uint8")).save(buffer, format="PNG")
    return buffer.getvalue()


def _scene(body=(190, 20, 25), background=(150, 152, 155)):
    """Fond gris uni, une « carrosserie » rectangulaire, une bande sombre (pneus) et un reflet blanc."""
    image = np.zeros((150, 200, 3)) + background
    mask = np.zeros((150, 200, 3))
    image[50:110, 40:160] = body
    image[100:110, 40:160] = (15, 15, 18)
    image[55:58, 60:140] = (250, 250, 250)
    mask[50:110, 40:160] = 255
    return _png(image), _png(mask)


def test_the_perceived_body_colour_and_its_distance_to_the_request() -> None:
    image, mask = _scene()
    out = render_metrics.measure(image, mask, requested="rouge")
    subject = out["subject"]
    assert subject["perceived_color"].startswith("rouge")
    assert subject["delta_e76_to_requested"] < 5
    assert subject["coverage_pct"] == pytest.approx(24.0, abs=0.1)
    assert subject["margins_px"] == [40, 50, 40, 40]
    assert subject["separation_delta_l"] > 10
    assert out["kind"] == "instrument_uncalibrated"


def test_a_washed_out_red_is_named_for_what_it_looks_like() -> None:
    """Le cas mesuré sur la voiture de Game Studio : rouge demandé, saumon clair perçu."""
    image, mask = _scene(body=(236, 160, 150))
    subject = render_metrics.measure(image, mask, requested="rouge")["subject"]
    assert subject["perceived_color"] == "saumon clair"
    assert subject["delta_e76_to_requested"] > 40
    assert render_metrics.color_name((76.0, 28.0, 15.0)) == "saumon clair"


def test_exposure_without_a_mask_and_refusals() -> None:
    image, _ = _scene(background=(255, 255, 255))
    out = render_metrics.measure(image)
    assert out["subject"] is None and out["highlights_clipped_pct"] > 50
    small = _png(np.zeros((10, 10, 3)))
    with pytest.raises(ValueError, match="tailles différentes"):
        render_metrics.measure(image, small)
    with pytest.raises(ValueError, match="presque vide"):
        render_metrics.measure(image, _png(np.zeros((150, 200, 3))))
    assert render_metrics.available() is True
