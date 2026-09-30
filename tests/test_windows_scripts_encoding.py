"""Scripts PowerShell livrés à l'utilisateur : lisibles par Windows PowerShell 5.1.

Constaté sur un Windows réel (run 8 du workflow windows-desktop) : un .ps1 UTF-8 SANS BOM est lu par
Windows PowerShell 5.1 dans la page de codes ANSI ; « — » (E2 80 94) y devient « â€” » dont 0x94 est un
guillemet typographique, et le script entier devient illisible. Tout .ps1 non ASCII doit porter le BOM.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_every_non_ascii_powershell_script_has_a_utf8_bom() -> None:
    scripts = sorted((REPO / "setup").rglob("*.ps1"))
    assert scripts, "aucun script PowerShell trouvé"
    missing = [
        str(p.relative_to(REPO))
        for p in scripts
        if not p.read_bytes().isascii() and not p.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert missing == []
