"""Demandes de CRÉATION 3D (« Crée une scène de forêt », « J'aimerais une moto noire ») — par opposition aux
questions et aux commandes du mode 3D (« Comment exporter un .glb vers Unity ? », « Change la couleur du
tank »).

Décide ce que le Cerveau APEX fait d'un message en mode 3D quand Game Studio est là
(`apex.assistant.turns`).
Une mission Game Studio coûte cher (minutes de calcul, GPU) : elle n'est jamais lancée sur une intention
devinée. Trois issues, chacune avec sa raison écrite :

- `create` : demande de création claire (verbe ou souhait de création, objet concret, ni question ni
  négation) → mission lancée si l'utilisateur l'a permis, proposée sinon ;
- `ambiguous` : peut-être une création (question de capacité, « plus tard », ajout à un objet existant,
  « refais… ») → la mission est seulement PROPOSÉE, l'utilisateur confirme ;
- `none` : question, remerciement, commande sur l'existant, négation → rien.

Analyse par structure de phrase (acte de langage, verbe, objet), pas par liste de phrases connues : écrite
et mesurée sur des messages jamais vus (docs/CERVEAU_APEX_V2.md §9 septies). Ce n'est pas une compréhension
du message : ce qui n'entre dans aucune forme reconnue reste `none`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["create", "ambiguous", "none"]


@dataclass(frozen=True)
class Intent:
    kind: Kind
    reason: str


#: Apostrophe typographique (U+2019), ramenée à l'apostrophe droite.
_CURLY_APOSTROPHE = "\u2019"
_GREETING = re.compile(r"^(?:bonjour|bonsoir|salut|coucou|hello|hi|hey|ok|okay|alors|bon|merci|thanks|"
                       r"super|parfait|génial|top|stp|svp|please)\b[\s,!.:-]*")
_POLITE = re.compile(r"\b(?:s'il (?:te|vous) pla[iî]t|stp|svp|please|merci(?: beaucoup)?)\b")

#: Négation ou refus : rien n'est lancé, quel que soit le reste.
_NEGATION = re.compile(
    r"(?<![\w'])(?:ne|n')(?![\w])|(?<![\w])(?:pas besoin|inutile de|sans (?:créer|faire|lancer)|don't|"
    r"do not|not yet|no need|never|jamais|pas encore|pas maintenant)(?![\w])")
#: Ce qui a déjà été fait n'est pas une demande.
_PAST = re.compile(
    r"(?<![\w])(?:j'ai|tu as|as-tu|avez-vous|vous avez|on a|nous avons|il a|elle a|a-t-il)\s+(?:déjà\s+)?"
    r"(?:créé|fait|fabriqué|modélisé|généré|construit|conçu|dessiné|préparé|ajouté|terminé)(?![\w])"
    r"|(?<![\w])(?:i|you|we) (?:have |'ve |already )?(?:made|built|created|generated|designed|drew|drawn|"
    r"added)(?![\w])|(?<![\w])did you (?:make|build|create)(?![\w])")
#: Un message qui commence ainsi demande une explication, pas une création (« Comment créer… ? »).
_EXPLAIN = re.compile(
    r"^(?:comment|pourquoi|qu'est[- ]ce|c'est quoi|quel|quelle|quels|quelles|combien|quand|où|explique|"
    r"expliquez|faut-il|dois-je|puis-je|y a-t-il|est-ce normal|"
    r"how|why|what|which|where|when|explain|is|are|does|do i|can i|should i|shall i)(?![\w])")
#: Question sur ce que l'assistant sait faire : peut-être une demande, jamais une certitude.
_CAPABILITY = re.compile(
    r"^(?:sais-tu|savez-vous|tu sais|vous savez|est-il possible|est-ce possible|c'est possible|peut-on|"
    r"on peut|is it possible|are you able|do you know how|can you do)(?![\w])")
#: Hypothèse ou report : « si je crée… », « plus tard », « peut-être ».
_HYPOTHESIS = re.compile(r"(?<![\w])(?:si (?:je|tu|on|nous|vous)|if (?:i|you|we))(?![\w])")
_DEFERRAL = re.compile(r"(?<![\w])(?:plus tard|un jour|peut-être|pas tout de suite|maybe|later|perhaps|"
                       r"someday|bientôt)(?![\w])")

_VERBS = (
    # français : impératif, infinitif (« peux-tu créer »), 2e personne du pluriel
    r"crée|créer|créez|fabrique|fabriquer|fabriquez|modélise|modéliser|modélisez|génère|générer|générez|"
    r"construis|construire|construisez|conçois|concevoir|concevez|sculpte|sculpter|sculptez|réalise|"
    r"réaliser|réalisez|monte|monter|montez|dessine|dessiner|dessinez|prépare|préparer|préparez|ajoute|"
    r"ajouter|ajoutez|produis|produire|produisez|imagine|imaginer|imaginez|fais|faire|faites|donne|donner|"
    r"donnez|"
    # subjonctif (« je veux que tu crées une… »)
    r"crées|fasses|modélises|dessines|prépares|ajoutes|construises|génères|fabriques|conçoives|"
    # anglais
    r"make|build|create|generate|model|design|craft|sculpt|draw|prepare|add|produce|give me"
)
_REDO = r"refais|refaire|refaites|recrée|recréer|régénère|régénérer|redo|remake|rebuild|recreate|regenerate"
_DETERMINERS = (r"un|une|des|deux|trois|quatre|cinq|six|dix|le|la|les|l'|mon|ma|mes|notre|nos|ce|cet|cette|"
                r"ces|du|de la|quelques|plusieurs|a|an|some|the|my|two|three|four|five|ten|several|\d+")
_DEFINITE = frozenset({"le", "la", "les", "l'", "mon", "ma", "mes", "notre", "nos", "ce", "cet", "cette",
                       "ces", "the", "my"})
_CLITIC = r"(?:-moi|-nous|-en|-m'en|\s+moi|\s+nous|\s+me|\s+us)?"
_VERB_LED = re.compile(
    rf"(?<![\w-])(?P<verb>{_VERBS}){_CLITIC}\s+(?P<det>{_DETERMINERS})(?![\w])(?P<rest>.*)")
_REDO_LED = re.compile(rf"(?<![\w-])(?:{_REDO}){_CLITIC}\s+(?:{_DETERMINERS})(?![\w])")
_DESIRE = re.compile(
    r"(?<![\w])(?:je (?:voudrais|veux|souhaite|souhaiterais|désire|aimerais|adorerais)|"
    r"j'(?:aimerais|adorerais|voudrais)|il (?:me|nous) (?:faut|faudrait)|"
    r"(?:j'ai|on a|nous avons|j'aurais|on aurait) besoin (?:d'|de )|"
    r"i(?: need| want| would like|'d like| would love|'d love)|we need|we want)\s*"
    rf"(?P<det>{_DETERMINERS}|un|une)(?![\w])(?P<rest>.*)")
#: Groupe nominal seul (« Une hache de bûcheron. ») : une demande s'il n'est pas une question.
_NOUN_PHRASE = re.compile(r"^(?P<det>un|une|des|deux|trois|a|an|two|three)\s+(?P<rest>\w.*)")

#: Objets qui ne sont pas des créations 3D (« Fais le point », « Crée un fichier », « Prépare un résumé »).
def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_ABSTRACT = _words("""
point résumé bilan liste récapitulatif récap pause test tests essai export import sauvegarde copie analyse
rapport recherche commentaire explication question réponse choix comparaison tableau code script scripts
fonction fichier fichiers dossier commit branche note notes message mail email courriel traduction texte
paragraphe phrase calcul estimation devis budget planning plan conseil avis exemple description erreur bug
correction projet compte conversation volume idée résolution partie
summary list recap report export import backup copy analysis comparison explanation function file
files folder branch note message email translation text paragraph sentence answer choice plan example
description estimate budget schedule break review fix update project account conversation idea
""")
#: Adjectifs placés avant le nom (« une petite maison ») : le nom qui suit décide.
_ADJECTIVES = _words("""
petit petite petits petites grand grande grands grandes nouveau nouvel nouvelle nouveaux nouvelles beau bel
belle beaux belles vieux vieil vieille gros grosse joli jolie jolis jolies autre autres seul seule premier
première dernier dernière vrai vraie superbe magnifique simple simples énorme immense minuscule
big small new old little nice simple huge tiny large beautiful other first last low-poly
""")
#: Ce à quoi on ajoute un nouvel objet (« Ajoute un bâtiment à mon projet ») — sinon c'est une modification
#: d'un objet existant (« Ajoute des roues à la voiture »), qui reste à confirmer.
_CONTAINERS = _words("""
projet scène scene niveau jeu monde décor carte map environnement univers village ville quartier garage
terrain collection assets project level game world environment city town
""")
_TARGET = re.compile(r"(?<![\w])(?:à|au|aux|dans|sur|to|into|in|on)\s+(?:la |le |les |l'|mon |ma |mes |"
                     r"ton |ta |tes |notre |nos |votre |vos |ce |cette |ces |the |my |our |this )?"
                     r"(?P<noun>[\w-]+)")
_MODIFIER = re.compile(
    r"(?<![\w])(?:plus|moins|trop|aussi|more|less|bigger|smaller|longer|shorter)(?![\w])")
_WORD = re.compile(r"[\w'-]+")


def _normalize(message: str) -> str:
    text = message.replace(_CURLY_APOSTROPHE, "'").lower()
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[tuple[str, bool]]:
    """(phrase, est-une-question) ; découpe aux fins de phrase et aux retours à la ligne."""
    parts = re.findall(r"[^.!?;\n]+[.!?;]*", text)
    out = []
    for part in parts:
        sentence = part.strip()
        if not sentence:
            continue
        question = sentence.endswith("?")
        sentence = _POLITE.sub("", sentence.rstrip(".!?; ")).strip(" ,")
        previous = None
        while previous != sentence:  # « Bonjour, merci ! » : plusieurs formules d'ouverture
            previous, sentence = sentence, _GREETING.sub("", sentence).strip()
        if sentence:
            out.append((sentence, question))
    return out


def _abstract(rest: str) -> bool:
    """Le nom de l'objet (premier mot après les adjectifs) désigne-t-il autre chose qu'une création 3D ?"""
    words = [w.strip("'-") for w in _WORD.findall(rest)]
    head = next((w for w in words if w not in _ADJECTIVES), "")
    if head == "plan" and "d'eau" in words[:3]:
        return False  # un plan d'eau est un objet de décor
    return head in _ABSTRACT


def _sentence(sentence: str, question: bool) -> Intent:
    if _NEGATION.search(sentence):
        return Intent("none", "négation ou refus")
    if _PAST.search(sentence):
        return Intent("none", "ce qui a déjà été fait")
    if _EXPLAIN.match(sentence):
        return Intent("none", "question d'explication")
    match = _VERB_LED.search(sentence) or _DESIRE.search(sentence)
    if match is None:
        if _REDO_LED.search(sentence):
            return Intent("ambiguous", "refaire un objet existant ?")
        noun = _NOUN_PHRASE.match(sentence)
        if noun is not None and not _abstract(noun.group("rest")):
            return (Intent("ambiguous", "un objet nommé, sous forme de question") if question
                    else Intent("create", "un objet demandé"))
        return Intent("none", "aucune demande de création reconnue")
    det, rest = match.group("det"), match.group("rest")
    if _abstract(rest):
        return Intent("none", "objet qui n'est pas une création 3D")
    if _CAPABILITY.match(sentence):
        return Intent("ambiguous", "question sur ce que sait faire l'assistant")
    if _HYPOTHESIS.search(sentence):
        return (Intent("none", "hypothèse") if question else Intent("ambiguous", "hypothèse"))
    if _DEFERRAL.search(sentence):
        return Intent("ambiguous", "création repoussée ou incertaine")
    verb = match.groupdict().get("verb") or ""
    if verb.startswith(("ajout", "add")):
        target = _TARGET.search(rest)
        if target is not None and target.group("noun") not in _CONTAINERS:
            return Intent("ambiguous", "ajout à un objet existant : une modification ?")
    if det in _DEFINITE and _MODIFIER.search(rest):
        return Intent("ambiguous", "modification d'un objet existant ?")
    if question and det in ("des", "some", "quelques", "plusieurs", "several"):
        return Intent("ambiguous", "question générale")
    return Intent("create", "demande de création")


def analyze(message: str) -> Intent:
    """Ce que demande le message : `create`, `ambiguous` ou `none`, avec la raison. Une phrase négative
    (« Ne lance rien ») l'emporte sur tout ; sinon la phrase la plus engageante décide."""
    results = [_sentence(s, q) for s, q in _sentences(_normalize(message))]
    if not results:
        return Intent("none", "message vide")
    negation = next((r for r in results if r.reason == "négation ou refus"), None)
    if negation is not None:
        return negation
    for kind in ("create", "ambiguous"):
        found = next((r for r in results if r.kind == kind), None)
        if found is not None:
            return found
    return results[0]


def asks_to_create(message: str) -> bool:
    """Demande de création claire (`analyze(...).kind == "create"`)."""
    return analyze(message).kind == "create"
