# APEX — Audit P2 -> P3 et roadmap recommandée

Point de départ : `25244e5` (fin de Priority 2). Chaque affirmation porte son statut :
**OBSERVED** (vu une fois), **TESTED** (test automatisé, souvent synthétique), **MEASURED** (mesure
réelle répétable), **JUDGED** (décision du Judge indépendant sur mesures réelles), **INFERRED**
(interprétation plausible, non prouvée), **UNVERIFIED**, **BLOCKED** (impossible dans cet environnement).

## 1. Ce qui est réellement acquis après P2

| affirmation | statut | preuve |
|---|---|---|
| le cerveau natif (0,72 M paramètres) apprend la réparation de code à partir de code source réel | JUDGED | coût held-out 0,9465 -> 0,749, 6 -> 24/81 bugs, deux promotions (`docs/CAPABILITIES.md` §10) |
| la compétence généralise à des fonctions jamais vues | JUDGED | held-out (dont la famille parsing) + 13 fonctions fraîches écrites après le gel (IC [0,026 ; 0,291]) |
| la première promotion est de la généralisation pure | JUDGED | aucune solution de tâche dans l'entraînement vérifié par rejeu |
| mémorisation et généralisation sont distinguées | JUDGED | `classify_learning` sur les comparaisons du Judge et les documents rejoués |
| la boucle REJECT -> diagnostic -> stratégie -> ACCEPT fonctionne sans humain | JUDGED | E2E final, 10 expériences choisies par la boucle |
| le diagnostic « 13,7 passages = sur-apprentissage » est la cause | INFERRED | `more_data` a ensuite été accepté ; aucune expérience contrôlée n'isole la cause |
| reproductibilité | OBSERVED | une expérience identique dans deux mondes construits séparément (une seule paire) |
| les garanties anti-fraude résistent | TESTED | 12 attaques synthétiques sur le vrai harness / Judge ; 42/42 mutants tués |
| transfert vers la sécurité par le champion promu | non démontré | 0,882 -> 0,826, non significatif |
| raisonnement | non démontré | aucune capacité de raisonnement n'existe |
| fonctionnement sur la machine de l'utilisateur (Windows / WSL / Docker / GPU) | UNVERIFIED / BLOCKED | pas de GPU ni de Docker ici |

## 2. Problèmes réels trouvés dans le code

1. **Le cerveau n'a jamais produit de solution évaluée.** `code_repair` mesure un CLASSEMENT de
   candidats énumérés : l'espace de sortie est fermé par le harness. `ApexLM.generate` existe mais
   aucune capacité ni la boucle ne l'utilise (OBSERVED, `apex/neural/model.py`).
2. **La génération est inutilisable pour l'évaluation** : décodage séquence par séquence, sans cache,
   sans arrêt anticipé. MEASURED : 1 000 problèmes courts ≈ 60 s par évaluation ; avec un décodage par
   lots et arrêt quand toutes les séquences ont terminé : quelques secondes.
3. **L'expérience n'atteint jamais le modèle comme donnée.** La boucle apprend des HYPER-PARAMÈTRES
   (mélange, jetons, lr) ; les succès / échecs par tâche (`capability_details`, `task_changes`) sont
   journalisés mais **aucune décision ne les lit** (OBSERVED : ni `strategy` ni `observe` n'y
   accèdent). « Apprendre de ses erreurs » n'existe qu'au niveau méta.
4. **Ajouter une capacité remet le cerveau à zéro.** Chaque monde entraîne SON tokenizer sur ses
   données, et un checkpoint ne se charge que si l'empreinte du dataset et le tokenizer sont ceux du
   monde (OBSERVED, `world.py:171`, `execute.py:104`). Une deuxième capacité = nouveau monde = toutes
   les promotions précédentes perdues. C'est l'obstacle principal à P3 (dans le même cerveau) et à P5.
5. **Interférence entre compétences dans un modèle minuscule** : 7 des 10 rejets de l'E2E final sont
   des régressions de la modélisation du texte (MEASURED). Toute nouvelle capacité sera en compétition
   de capacité avec les anciennes.
6. **Objectif d'entraînement non adapté aux tâches** : fenêtres aléatoires sur un flux concaténé, perte
   sur tous les jetons ; un exemple question -> réponse peut être coupé, et l'essentiel du gradient sert
   à prédire l'énoncé (OBSERVED ; coût réel : INFERRED).
7. **Sélection de la faiblesse** : l'observation prend les capacités dans l'ordre de déclaration ;
   aucune priorisation par gain attendu entre plusieurs capacités (lacune P5).
8. **Système parallèle hérité** : ≈ 17 000 lignes (`metalearner` 8 350, `improvement`, `taskgen`,
   `taskloop`, `evalrunner`…) dont « l'apprentissage » règle des prompts / génomes autour d'un modèle
   EXTERNE (Ollama via `ModelProvider`). Aucun import depuis le cerveau natif (OBSERVED). Il ne fait
   pas partie des preuves d'apprentissage d'APEX ; à traiter dans la mission de nettoyage.
9. **Statistique** : 27 fonctions held-out, 13 fraîches -> intervalles larges ; objectif du contrat
   (0,25) loin.
10. **Tokenizer** : chiffres = jetons isolés (bon pour l'arithmétique), mais « 1 » et « ␣1 » sont des
    jetons différents : un format compact sans espaces est nécessaire pour le calcul.
11. Registre des interfaces : cohérent dans le dépôt de développement (0 « mensonge ») ; le contrôle
    `doctor` échoue seulement dans l'artefact Runtime où les modules Research sont exclus (déjà couvert
    par les 4 échecs pré-existants de packaging).

## 3. Expérience de faisabilité P3 (avant toute architecture)

*(résultats ci-dessous)*
