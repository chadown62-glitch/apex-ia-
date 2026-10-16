"""Demandes de CRÉATION 3D (« Crée une scène de forêt », « Il me faut une moto noire ») — par opposition
aux questions du mode 3D (« Comment exporter un .glb vers Unity ? »).

Sert de garde-fou à la délégation du Cerveau APEX vers Game Studio (`apex.assistant.turns`) : le Cerveau
reconnaît une demande 3D par son appel 3D appris, mais il en fait aussi à tort sur des messages qui n'en
sont pas (mesuré sur 30 messages jamais vus du mode 3D : 7 ; docs/CERVEAU_APEX_V2.md §9 sexies). Une
mission Game Studio coûte cher (minutes de calcul, GPU) : elle n'est lancée que si le Cerveau a reconnu
une demande 3D ET que le message est formulé comme une demande de création (mesuré : 0 lancement à tort
sur ces 30 messages, 22 demandes de création confiées sur 30).

Repérage par formulations explicites, écrites ici puis mesurées sur des messages jamais vus : une demande
sans aucune de ces formulations (« Prépare… », « Ajoute… », « Dessine-moi… ») n'est pas reconnue. Ce n'est
pas une compréhension du message."""

from __future__ import annotations

import re

#: Apostrophe typographique (U+2019), ramenée à l'apostrophe droite.
_CURLY_APOSTROPHE = "\u2019"
_GREETING = re.compile(r"^(?:bonjour|bonsoir|salut|coucou|hello|hi|hey|ok|alors|stp|svp)\b[\s,!.]*")
#: Un message qui commence ainsi demande une explication, pas une création (« Comment créer… ? »).
_QUESTION = re.compile(
    r"^(?:comment|pourquoi|qu'est[- ]ce|quel|quelle|quels|quelles|combien|explique|expliquez|"
    r"où|how|why|what|which|where|explain)\b")
_VERBS = (
    # français (impératif, infinitif après « peux-tu », 2e personne du pluriel)
    r"crée|créer|créez|crées|fabrique|fabriquer|fabriquez|modélise|modéliser|modélisez|génère|générer|"
    r"générez|construis|construire|construisez|fais|faire|faites|monte|monter|montez|conçois|concevoir|"
    r"concevez|sculpte|sculpter|sculptez|réalise|réaliser|réalisez|"
    # anglais
    r"make|build|create|generate|model|design|craft|sculpt|give me"
)
_DETERMINERS = (r"un|une|des|deux|trois|quatre|cinq|le|la|les|l'|mon|ma|mes|notre|nos|ce|cet|cette|ces|"
                r"a|an|some|the|my|two|three|four|five|\d+")
_REQUEST = re.compile(
    r"(?<![\w])(?:"
    rf"(?:{_VERBS})(?:-moi|-nous| moi| nous| me| us)?\s+(?:{_DETERMINERS})(?![\w])"
    r"|besoin d'(?:un|une|des)\b|il me faut\b|il nous faut\b"
    r"|je (?:voudrais|veux|souhaite|souhaiterais|aimerais) (?:un|une|des|deux|trois)\b"
    r"|i (?:need|want|would like|'d like) (?:a|an|some|two|three)\b"
    r")")
#: Groupe nominal seul (« Une hache de bûcheron, s'il te plaît. ») : une demande, pas une question.
_NOUN_PHRASE = re.compile(r"^(?:un|une|des|a|an)\s+\w")


def asks_to_create(message: str) -> bool:
    """Le message est-il formulé comme une demande de création ? (insensible à la casse ; apostrophes
    typographiques normalisées ; une question d'explication n'en est jamais une)."""
    text = message.replace(_CURLY_APOSTROPHE, "'").strip().lower()
    text = _GREETING.sub("", text)
    if _QUESTION.match(text):
        return False
    if _REQUEST.search(text):
        return True
    return _NOUN_PHRASE.match(text) is not None and "?" not in text
