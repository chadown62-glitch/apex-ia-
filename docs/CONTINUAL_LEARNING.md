# Apprentissage continu, oubli, P4, P5, transfert

Suite de `docs/P3_REASONING.md`. Statuts : IMPLEMENTED, TESTED, MEASURED, REAL E2E, GENERALIZED,
ADVERSARIAL, PROMOTED, et **HYPOTHESIS REJECTED** quand une hypothèse est réfutée. Tout a été
exécuté dans le bac à sable de développement (Linux, 4 cœurs, sans GPU) ; rien ne valide la
machine de l'utilisateur.

## 1. Audit de départ (commit `9560cdf`)

- Les gardes du Judge comparent le candidat au SEUL champion courant : une suite de promotions dont
  chacune perd un peu, dans la tolérance ou sous le seuil de signification, peut faire dériver une
  capacité sans limite (aucune ancre du niveau démontré).
- Aucune mesure formelle de l'oubli : les régressions apparaissent comme `protected_regression`
  (suites dégradées contre le champion), sans « avant / après » par capacité.
- La boucle sait déjà réagir à une régression mesurée (`protect_domains` : plus de rejeu des domaines
  dégradés ; `lower_lr`) : réutilisé, aucun moteur parallèle créé.
- Une expérience dirigée n'admet qu'UNE modification par rapport à sa recette de référence : les bras
  ci-dessous déclarent leur référence (taux d'apprentissage) et ne changent que le mélange.

## 2. Oubli mesuré : reprise du champion P3 (MEASURED)

Protocole : même monde, même champion adopté (P3 promu), même cible, même budget (1 500 pas,
600 000 jetons tirés, lots de 16 × 256), un entraînement par bras, jugé par le Judge (rejeu compris).
Oubli d'une suite = valeur après − valeur avant (métriques « plus bas = mieux »).

| bras | lr | données anciennes | `program_trace` held-out | BPB texte held-out (oubli) | pire oubli | verdict |
|---|---|---|---|---|---|---|
| A régime actuel | 1e-3 | 48 % | 0,283 -> **0,233** | 1,801 -> 2,108 (+0,307) | stdlib +0,445 | REJECT |
| B lr réduit | 1e-4 | 48 % | 0,283 -> 0,293 (n.s.) | 1,801 -> 1,865 (+0,064) | stdlib +0,100 | REJECT |
| C rejeu | 1e-3 | 70 % | 0,283 -> 0,327 (pire) | 1,801 -> 2,031 (+0,230) | stdlib +0,332 | REJECT |
| D lr réduit + rejeu | 1e-4 | 70 % | 0,283 -> 0,313 (n.s.) | 1,801 -> 1,848 (+0,048) | stdlib +0,076 | REJECT |
| E = D + 4x données tirées (2,4 M) | 1e-4 | 70 % | 0,283 -> 0,303 (n.s.) | 1,801 -> **1,766 (−0,035)** | aucun | INCONCLUSIVE |
| F = A + 4x données tirées (2,4 M) | 1e-3 | 48 % | 0,283 -> **0,233** | 1,801 -> **1,784 (−0,017)** | `code_repair` validation +0,031 | ACCEPT, réplique REJECT |

Comparaisons appariées entre bras (IC 95 % de l'écart) :

- le taux d'apprentissage est le facteur DOMINANT de l'oubli : A -> B divise l'oubli du texte par ≈ 5
  (écart [0,239 ; 0,256] BPB) ; C -> D aussi ([0,181 ; 0,196]) ;
- le rejeu réduit l'oubli, moins : A -> C −0,077 BPB, B -> D −0,017 BPB (significatifs) ;
- seul le bras A APPREND (−0,05 d'erreur, significatif contre B, C, D) — et c'est lui qui oublie le
  plus ; `code_repair` : aucune différence significative entre bras ;
- coût : ≈ 215–240 s d'entraînement, ≈ 575–610 s de bout en bout par bras (Judge et rejeu compris).

Bras E (une modification par rapport à D : 2,4 M jetons tirés au lieu de 600 000, soit ≈ 2,5
passages sur l'échantillon au lieu de ≈ 10) : **plus aucun oubli** — le texte S'AMÉLIORE
significativement (IC [0,034 ; 0,037]), `code_repair` est stable ; la cible n'est pas améliorée
(IC [−0,057 ; 0,010]). L'oubli résiduel de D venait de la RELECTURE d'un petit échantillon.

Bras F (une modification par rapport à A : 2,4 M jetons tirés) : la cible est apprise autant que
dans A (0,233 ; IC du gain [0,013 ; 0,090]) et le texte S'AMÉLIORE (1,784, IC [0,015 ; 0,019]) au
lieu d'oublier (+0,307 pour A ; écart A -> F [0,320 ; 0,344]). Premier jugement : ACCEPT
(`code_repair` held-out même significativement meilleur, 0,803 -> 0,671). **Réplique (graine
1000003) : REJECT** — cible confirmée (IC [0,01 ; 0,09]), texte confirmé (1,753), mais
`code_repair` validation régresse significativement (0,469 -> 0,568, IC [−0,198 ; −0,006], n = 18).
Non promu. L'effet de F sur `code_repair` dépend de la graine : ni amélioration ni oubli établis.

Conclusions (MEASURED, un entraînement par bras + une réplique pour F) :

- la cause principale de l'oubli du TEXTE à ce budget est la **relecture** d'un petit échantillon
  (≈ 10 passages) : à taux égal, 4x plus de données tirées supprime l'oubli (A -> F, D -> E) ;
- réduire le taux (B, D, E) réduit l'oubli mais **supprime l'apprentissage** de la cible (écart
  E -> F sur la cible [0,03 ; 0,11] en faveur de F) : l'hypothèse « le taux remis au maximum est la
  cause principale de l'oubli » est **HYPOTHESIS REJECTED** comme explication principale — c'est un
  facteur, pas la cause (F oublie moins que D avec un taux 10x plus élevé) ;
- le rejeu (C, D) aide peu et coûte de l'apprentissage ;
- F est la meilleure stratégie mesurée (apprend, n'oublie pas le texte) mais **n'est pas promue** :
  sa réplique oublie `code_repair` validation. Le Judge (réplique comprise) a refusé les six bras
  qu'il devait refuser ; aucune capacité acquise n'a été détruite dans le cerveau promu.

## 3. Protection des capacités acquises (IMPLEMENTED, TESTED)

`apex/neural/protection.py`, branché dans le Judge (`judge(..., protections=)`), le registre
(`ModelRegistry(protected_groups=)`) et l'analyse d'échec de la boucle. Groupes protégés
(`World.protected_groups`) : le texte (suite principale + benchmarks texte) et chaque capacité
(suite d'acceptation + suites du contrat).

Pour chaque groupe, une **ancre** : l'entrée PROMUE et attestée du registre qui a démontré le meilleur
niveau sur la suite-clé, parmi les versions mesurées sur les MÊMES benchmarks (empreintes égales).
L'ancre conserve : benchmark (empreinte), version du modèle (index, nom, artefact), mesures brutes
par élément, métrique, tolérance, historique de toutes les versions comparables.

- **Oubli** d'une suite = après − avant (contre l'ancre, pas contre le seul champion) ; statut
  FORGOTTEN (dégradation significative au bootstrap apparié ou au-delà de la tolérance), IMPROVED
  (amélioration significative), NOISE ; mesures brutes, IC et n conservés dans le jugement.
- **Vérification par le Judge** : il remesure lui-même l'ancre depuis son checkpoint ; si les
  mesures enregistrées ne sont plus celles du checkpoint (registre falsifié), le jugement est REJECT
  (`anchor_<groupe>`) et aucun oubli n'est calculé contre une ancre invérifiable.
- **Refus d'une promotion destructrice** : le Judge ET le registre rejettent tout candidat avec une
  suite FORGOTTEN. Test de dérive cumulée : quatre versions, chacune perd 1 tâche sur 40 (dans la
  tolérance) contre la précédente ; sans ancre la 4e est promue, avec ancre elle est refusée
  (0,575 contre 0,50 démontré).
- **Boucle** : une réplique qui gagne sur la cible mais dégrade une suite protégée est analysée comme
  `protected_regression` (-> stratégie `protect_domains`), et non plus comme « non répliqué »
  (-> plus de pas), ce qui aurait aggravé l'oubli. Défaut trouvé par le bras F, corrigé et testé.

## 4. P4 — profondeur : protocole PRÉ-ENREGISTRÉ (écrit et commité avant toute exécution)

Question : l'échec en profondeur 4 (le modèle écrit toujours 4 valeurs) vient-il de la TAILLE, de la
REPRÉSENTATION ou des DONNÉES ? Outil : `scripts/p4_depth_lab.py` (composants de production :
`ApexLM`, `TrainingEngine`, `TokenBatcher`, `generate_batch`, générateur et évaluateur de
`program_trace`). Un bras = un facteur changé par rapport à `base`, à budget égal (6 000 pas, lots de
32 × 256 jetons : ≈ 49 M jetons vus), même tokenizer (celui du monde), mêmes problèmes d'évaluation.

| bras | facteur changé | profondeurs entraînées |
|---|---|---|
| `base` | — (architecture du cerveau, 0,72 M paramètres, format `a3,b7,c4=4`) | 1–3 |
| `big` | modèle ≈ 4x (2,87 M paramètres : 6 couches, largeur 192) | 1–3 |
| `explicit` | chaque étape recopie l'instruction exécutée : `a=3>3,b=a+4>7,c=b-a>4=4` | 1–3 |
| `curriculum` | 25 % des problèmes d'entraînement en profondeur 4 | 1–4 |

Évaluation : profondeur 3 = suite held-out protégée (300), profondeur 4 = suite `deeper` protégée
(200), profondeur 5 = 200 problèmes neufs (forme canonique absente de tout entraînement et de toute
évaluation, noms tirés au hasard) ; exactitude de TOUTE la trace, longueur de trace écrite.

**Critère de réussite (fixé ici, avant les résultats)** : un bras réussit la profondeur d si la borne
inférieure de l'IC bootstrap à 95 % de son exactitude en profondeur d est > 0,10. La généralisation en
longueur n'est établie qu'à une profondeur JAMAIS entraînée par le bras (5 pour tous ; 4 aussi sauf
`curriculum`). La profondeur 3 ne doit pas régresser par rapport à `base`. Un bras qui réussit est
REJOUÉ avec une seconde graine avant toute conclusion ; seul un bras répliqué peut être intégré au
cerveau (par une expérience jugée par le Judge, avec les protections).
