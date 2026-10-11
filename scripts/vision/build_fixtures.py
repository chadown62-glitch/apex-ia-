"""Reproducible synthetic image fixtures; these are not photographs from users."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def build_fixtures(output: Path) -> list[dict[str, str | None]]:
    output.mkdir(parents=True, exist_ok=True)
    fonts = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")
    font_path = next((f for f in fonts if Path(f).is_file()), None)
    font = ImageFont.truetype(font_path, 36) if font_path else ImageFont.load_default(size=36)
    fixtures: list[dict[str, str | None]] = []
    image = Image.new("RGB", (1100, 650), "white")
    draw = ImageDraw.Draw(image)
    draw.text((65, 65), "EXERCICE DE MATHEMATIQUES", font=font, fill="black")
    draw.text((65, 180), "Resoudre : 2x + 3 = 11", font=font, fill="black")
    draw.text((65, 265), "Donner la valeur de x.", font=font, fill="black")
    # JPEG encoding and a slight affine transform emulate acquisition, without claiming a real photo.
    image.transform(
        image.size,
        Image.Transform.AFFINE,
        (1, 0.035, -12, 0.012, 1, -8),
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    ).save(output / "exercise.jpg", quality=88)
    fixtures.append({"name": "exercise.jpg", "question": "Résous l'équation et donne x.", "expected": "4"})

    image = Image.new("RGB", (1100, 850), "white")
    draw = ImageDraw.Draw(image)
    draw.text((50, 45), "SCIENCES - MESURES", font=font, fill="black")
    draw.text((50, 125), "Masse volumique = masse / volume", font=font, fill="black")
    for y in (230, 320, 410):
        draw.line((50, y, 1050, y), fill="black", width=3)
    for x in (50, 430, 760, 1050):
        draw.line((x, 230, x, 410), fill="black", width=3)
    for x, text in ((75, "Objet"), (455, "Masse (g)"), (780, "Volume (cm3)")):
        draw.text((x, 250), text, font=font, fill="black")
    for x, text in ((75, "Echantillon A"), (455, "6"), (780, "2")):
        draw.text((x, 340), text, font=font, fill="black")
    draw.text((50, 500), "Calculer la masse volumique de A.", font=font, fill="black")
    draw.rectangle((100, 650, 300, 790), outline="black", width=4)
    draw.text((370, 695), "Schema de l'echantillon A", font=font, fill="black")
    image.save(output / "document.png")
    fixtures.append(
        {
            "name": "document.png",
            "question": "Lis le tableau. Quelle est la masse volumique de A ?",
            "expected": "3",
        }
    )

    image = Image.new("RGB", (1100, 850), "white")
    draw = ImageDraw.Draw(image)
    draw.text((55, 40), "GRAPHIQUE - QUANTITES", font=font, fill="black")
    draw.line((150, 140, 150, 720, 970, 720), fill="black", width=4)
    for n in range(6):
        y = 720 - n * 95
        draw.text((90, y - 20), str(n), font=font, fill="black")
        draw.line((140, y, 960, y), fill="#dddddd", width=1)
    draw.rectangle((280, 530, 420, 719), fill="#397ac2", outline="black")
    draw.rectangle((650, 245, 790, 719), fill="#c45e3a", outline="black")
    draw.text((330, 755), "A", font=font, fill="black")
    draw.text((695, 755), "B", font=font, fill="black")
    draw.text((325, 480), "2", font=font, fill="black")
    draw.text((695, 195), "5", font=font, fill="black")
    image.save(output / "chart.png")
    fixtures.append(
        {"name": "chart.png", "question": "Que valent A et B ? Calcule B moins A.", "expected": "3"}
    )

    Image.new("RGB", (1000, 700), "white").save(output / "blank.png")
    fixtures.append(
        {
            "name": "blank.png",
            "question": "Résous l'exercice si l'image contient un énoncé.",
            "expected": None,
        }
    )
    Image.open(output / "exercise.jpg").filter(ImageFilter.GaussianBlur(14)).save(output / "ambiguous.png")
    (output / "corrupt.jpg").write_bytes(b"not an image")
    (output / "cases.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixtures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(build_fixtures(parser.parse_args().output), ensure_ascii=False))
