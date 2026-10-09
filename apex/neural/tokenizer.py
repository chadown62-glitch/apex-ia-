"""Tokenizer propre à APEX (BPE au niveau octet) — défini dans `apex.brain.tokenizer` (paquet Runtime : le
Cerveau APEX est servi par l'application), entraîné ici (recherche). Une seule définition, réexportée."""

from apex.brain.tokenizer import SPECIAL_TOKENS, ByteBPETokenizer

__all__ = ["SPECIAL_TOKENS", "ByteBPETokenizer"]
