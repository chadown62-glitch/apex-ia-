"""Extraction du texte d'un contenu acquis. Un format non pris en charge
renvoie `None` avec la raison — jamais un texte inventé."""

from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True)
class Extraction:
    text: str | None
    method: str
    fmt: str
    detail: str


class _TextOnly(HTMLParser):
    """Texte visible seulement. Une pile de balises ouvertes suit les zones
    ignorées (script, style…) et masquées (CSS `display:none`,
    `visibility:hidden`, attribut `hidden`) : le texte masqué est mis à part,
    jamais mêlé au contenu — c'est un vecteur classique d'injection."""

    _SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    _BLOCK = frozenset(
        {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre"}
    )
    _VOID = frozenset(
        {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
        | {"track", "wbr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []  # (balise, ignorée, masquée)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        style = (attributes.get("style") or "").replace(" ", "").lower()
        hidden = "display:none" in style or "visibility:hidden" in style or "hidden" in attributes
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag not in self._VOID:
            self._stack.append((tag, tag in self._SKIP, hidden))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        # Referme jusqu'à la balise correspondante (HTML mal formé toléré) ;
        # une fermeture orpheline est ignorée.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if any(skip for _, skip, _ in self._stack):
            return
        (self.hidden if any(h for _, _, h in self._stack) else self.parts).append(data)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def extract(data: bytes, content_type: str | None) -> Extraction:
    kind = (content_type or "").lower()
    if kind in ("text/plain", "text/markdown", "text/x-python", "text/x-rst"):
        return Extraction(_decode(data), "decode-utf8", kind.split("/")[-1], "texte brut")
    if kind in ("text/html", "application/xhtml+xml"):
        parser = _TextOnly()
        parser.feed(_decode(data))
        detail = "balises retirées"
        if "".join(parser.hidden).strip():
            detail += " ; texte masqué (CSS/hidden) écarté du contenu"
        return Extraction("".join(parser.parts), "html-text", "html", detail)
    if kind == "application/json":
        try:
            value = json.loads(_decode(data))
        except ValueError:
            return Extraction(None, "json", "json", "JSON invalide")
        strings: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, str):
                strings.append(node)
            elif isinstance(node, dict):
                for item in node.values():
                    walk(item)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(value)
        return Extraction("\n".join(strings), "json-strings", "json", "chaînes du document")
    return Extraction(
        None, "none", kind or "inconnu", f"format « {kind or 'inconnu'} » non extractible ici"
    )
