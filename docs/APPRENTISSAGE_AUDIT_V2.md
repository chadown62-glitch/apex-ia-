# Pourquoi le protocole v1 n'a montré aucune amélioration — audit, et protocole v2 pré-enregistré

Rédigé le 05/10/2026. Sources : le code (lu), le run 25 (v1, modèle réel 1.5B en CI, `docs/EXPERIENCE_RUN1_RUN2.md`
§4.1). **Aucune stratégie n'a été modifiée.** Le protocole v2 ci-dessous est une PROPOSITION figée avant toute
mesure ; il n'est pas implémenté (il exige un nouveau bras de stratégie, à approuver).

Règle respectée : aucune stratégie n'est conçue à partir des RUN 2 observés. Les seules données v1 utilisées ici
sont (a) la variabilité entre RUN 1 et RUN 2 baseline — deux bras SANS expérience, pour dimensionner le protocole —
et (b) la mécanique de la règle (températures appliquées), jamais le contenu des réponses du bras conditionné.

## 0. Machine cible (préalable) — UNVERIFIED

La validation sur la machine cible (RTX 4060 Ti 8 Go, i5-14400F, 16 Go, WSL2, Ollama, qwen2.5-coder:7b-instruct,
gemma4:12b, bge-m3) n'a PAS été exécutée : elle exige la machine de l'utilisateur. Outil prêt (aucune
installation, `.env`/`config.yaml` non modifiés) :

```
powershell -ExecutionPolicy Bypass -File setup\packaging\windows\Prouver-Boucle-APEX.ps1 -AllowRestart -WithExperience -Replicates 4
```

Mesures produites : CPU, RAM, GPU/pilote (nvidia-smi Windows), commit APEX, modèles Ollama présents (alerte si
`qwen2.5-coder:7b-instruct` absent) ; démarrage par le Panel (Startup Gate) ; modèle sélectionné ; durée de l'appel
et durées Ollama (chargement, prompt, génération) ; VRAM utilisée (nvidia-smi dans WSL) et part du modèle en VRAM
(`/api/ps`) juste après chaque exécution ; TaskRun, sandbox, Judge, Experience ; persistance après un VRAI
redémarrage (changement de processus exigé) ; exécution interrompue ; puis l'expérience v1 (RUN 1 → RUN 2) avec
le 7B. Si le démarrage est refusé par `apex doctor` (`gpu.config_match`), la seule correction légitime est que
`.env` déclare le matériel réel (`APEX_GPU_VRAM_GB=8`), ce que fait déjà la configuration de référence du projet.

## 1. Audit

| Question | Constat | Statut |
|---|---|---|
| Ce que le RUN 1 conserve | `Experience` : verdict, catégorie d'échec, fitness, empreinte, modèle, stratégie. `EvalRun` : code généré, détail de l'Oracle (« k/N cas incorrects (indices) », exceptions), sortie du candidat, code de sortie, durées. | TESTÉ |
| Ce qui est récupéré | Les `Experience` du RUN 1 (même tâche, même TaskRun). La règle n'en lit que `failure_category`, `success_evidence`, `fitness`, `generation_config`. | TESTÉ |
| Ce qui est transmis au RUN 2 | Une température et l'UN de deux rappels CONSTANTS. Rien sur ce qui a échoué : ni k/N, ni la contrainte violée, ni le code précédent. ≈ 1 information (la catégorie). | TESTÉ |
| Le Judge identifie-t-il la nature de l'échec ? | Partiellement : il sait combien de cas échouent et si le candidat a levé des exceptions ; il ne sait pas QUELLE contrainte de l'énoncé est violée (les entrées cachées ne doivent pas sortir). | TESTÉ |
| L'Experience suffit-elle pour une stratégie utile ? | Non pour corriger une erreur de compréhension : elle ne porte ni le détail de l'Oracle ni le code. | INFÉRÉ |
| Le rappel générique est-il trop pauvre ? | `_INCORRECT_SOLUTION_HINT` parle de chaîne vide, d'un seul caractère et de casse — sans rapport avec l'erreur observée sur is_palindrome (ponctuation retirée) : 9/12 dans tous les bras conditionnés v1. | MESURÉ (v1) / INFÉRÉ (cause) |
| Catégorie mal choisie | `classify_failure` classe « candidat invalide » toute réponse dont UN cas lève une exception ; la règle applique alors T×0,3 et un rappel de FORMAT (markdown), sans rapport avec l'échec réel. Les T = 0,21 de rle_encode (v1) l'indiquent. | TESTÉ (règle) / INFÉRÉ (v1) |
| La température augmente-t-elle le bruit ? | `incorrect_solution` → T = 0,7 + 0,4 = 1,1 : c'est de l'EXPLORATION (rééchantillonnage plus aléatoire), pas une correction. Effet non mesuré séparément. | INFÉRÉ |
| Confusion exploration / correction | Oui : la règle ne dispose d'aucune information sur quoi corriger ; elle rééchantillonne. Une erreur systématique (palindrome : 7/8 échecs sans expérience) n'est pas visée ; une erreur aléatoire est déjà corrigée par un simple nouveau tirage (rle_encode : RUN 2 baseline 4/4). | MESURÉ (taux) / INFÉRÉ (mécanisme) |
| Bras non comparables | A = température du fournisseur (valeur exacte UNVERIFIED) ; B applique toujours une température explicite, même « inchangée » (0,7 après un succès). A et B diffèrent par la température ET le prompt ; l'effet de chaque levier n'est pas séparable. | TESTÉ |
| Répliques suffisantes ? | Non. 6 paires analysables, 1 seule discordante. Test du signe bilatéral α = 0,05 : il faut au moins 6 paires discordantes TOUTES favorables pour conclure ; puissance ≈ 0,26 même pour un effet fort (80 % des discordances favorables). ≈ 25–30 paires discordantes par comparaison sont nécessaires (puissance 0,8–0,9). | MESURÉ (calcul exact) |
| Tâches assez difficiles ? | Non : 3 tâches sur 5 réussies 4/4 au RUN 1 par le 1.5B → 14 paires sur 20 exclues ; le 7B en réussira probablement davantage (plafond). | MESURÉ (1.5B) / INFÉRÉ (7B) |
| Hasard masquant l'effet ? | Oui : entre RUN 1 et RUN 2 baseline (même stratégie, aucune expérience), 5 paires sur 20 sont discordantes (25 %, run 25) ; 6 sur 20 (30 %) à la réplication du run 26. Le bruit est du même ordre que tout effet plausible. | MESURÉ |
| Justification causale de la règle | Heuristique (règle N+20) : « réponse incorrecte → explorer », « candidat invalide → resserrer ». Aucune mesure antérieure de son effet sur un vrai modèle. | TESTÉ (code) |

**Conclusion de l'audit.** L'absence d'amélioration en v1 a quatre causes plausibles, non exclusives :
(1) le RUN 2 ne reçoit presque aucune information sur l'échec ; (2) la règle explore au lieu de corriger, et
range certaines erreurs dans la mauvaise catégorie ; (3) la comparaison n'avait quasiment aucune puissance
(1 paire discordante) ; (4) les tâches étaient trop faciles pour produire assez d'échecs. Le résultat v1
(« aucune différence démontrée ») reste valide : il ne dit pas que l'expérience est inutile.

## 2. Bras C : techniquement justifié ?

Oui, comme HYPOTHÈSE à tester, non comme amélioration attendue. Justification indépendante des résultats : la
règle actuelle transmet au plus une catégorie ; le Judge dispose d'autres faits NON sensibles (nombre de cas
échoués, exceptions levées, terminaison). C mesure si ces faits, transmis tels quels, changent la performance.

**Informations autorisées pour C (liste fermée)** : catégorie d'échec ; k cas en échec sur N ; nombre
d'exceptions et NOMS de leurs classes (ex. `IndexError`) ; script terminé ou non ; code de sortie.
**Interdites** : entrées cachées, sorties attendues, indices des cas, MESSAGES d'exception (ils peuvent contenir
une entrée cachée, ex. `KeyError('…')`), valeurs publiées par le candidat, code précédent (sortie de modèle, réservée
à un éventuel bras D d'un protocole ultérieur), tout texte propre à une tâche.

**Texte de C (figé)**, ajouté après l'énoncé, aucun conseil (l'effet doit venir de l'information, pas d'une
consigne) :
« Information sur une tentative précédente à cette même tâche : elle a été jugée incorrecte par des tests cachés
({k} cas sur {N} en échec{, dont E exception(s) : Classe1, Classe2}). Catégorie d'échec : {libellé}. »
Libellés figés : `incorrect_solution` → « résultats incorrects » ; `invalid_candidate` → « exception ou résultat
illisible » ; autres catégories → C n'est pas appliqué (paire exclue, voir §3).

## 3. Protocole v2 (pré-enregistré, non exécuté)

- **Modèles** : primaire `qwen2.5-coder:7b-instruct` sur la machine cible (seul résultat valant pour B). Réplication
  facultative `qwen2.5-coder:1.5b-instruct` en CI, analysée et rapportée SÉPARÉMENT, jamais agrégée.
- **Bras** : A = aucune expérience ; B = règle actuelle (`decide_strategy_from_experiences`, inchangée) ;
  C = A + texte du §2. **Température de base explicite T0 = 0,7 pour A, C et le RUN 1** (B garde ses mutations).
  Toute différence A/C ne vient donc que de l'information ; A/B réplique v1 avec une base explicite.
- **Tâches** : réserve = les 5 tâches v1 + 10 tâches nouvelles (même contrat : énoncé avec une contrainte précise,
  cas cachés, réponses hors sandbox), rédigées et validées (solution correcte = PASS, piège = FAIL) AVANT toute
  mesure, figées par un commit. **Calibration** (RUN 1 seuls, T0, 8 tirages par tâche, modèle primaire) : sont
  retenues les tâches réussies 1 à 6 fois sur 8. La sélection n'utilise que des RUN 1, jamais un RUN 2.
- **Instances** : pour chaque tâche retenue, une tâche neuve par instance ; RUN 1 à T0. Instance ÉLIGIBLE si le
  RUN 1 échoue avec une catégorie `incorrect_solution` ou `invalid_candidate`. Puis A, B et C, chacun conditionné
  (B, C) sur ce même RUN 1, ordre en carré latin (ABC, BCA, CAB, tournant par instance).
- **Taille et arrêt** : arrêt dès 60 instances éligibles ou 240 RUN 1 (le premier atteint). La règle d'arrêt ne
  dépend jamais des résultats des RUN 2. Aucune analyse intermédiaire.
- **Métrique primaire** : réussite (PASS) du RUN 2. **Comparaisons primaires** : C contre A et B contre A, test de
  McNemar exact (= test du signe sur les paires discordantes), bilatéral, correction de Holm sur ces 2 tests
  (α global 0,05).
- **Secondaires** : cas réussis (test du signe sur les différences) ; C contre B (exploratoire) ; transitions de
  catégorie d'échec ; empreintes identiques ; durées, jetons, VRAM.
- **Exclusions** (rapportées, jamais masquées) : RUN 1 réussi ou échec d'infrastructure (non éligible) ; tout RUN 2
  en erreur d'infrastructure (instance exclue des 3 comparaisons).
- **Décision** (par comparaison) :
  - p ajusté < 0,05 et gains > pertes → **amélioration mesurée** ;
  - p ajusté < 0,05 et pertes > gains → **régression mesurée** ;
  - sinon, avec au moins 20 paires discordantes → **aucune différence démontrée** ;
  - sinon (moins de 20 paires discordantes, plus de 10 % d'erreurs d'infrastructure, ou moins de 60 instances
    éligibles au plafond) → **protocole insuffisant pour conclure**.
- **« APEX apprend de ses expériences »** ne pourra être écrit que si C contre A ou B contre A conclut à une
  amélioration mesurée sur le modèle primaire.

## 4. Ce qu'il faudrait implémenter pour v2 (non fait, en attente d'approbation)

> **Statut au 05/10/2026 (ajout postérieur, le texte ci-dessus reste celui du pré-enregistrement)** : les points
> 1 à 4 sont implémentés et testés avec une doublure de modèle ; l'expérience n'est PAS exécutée et la machine
> cible reste UNVERIFIED. Écarts d'implémentation (tous antérieurs à toute donnée v2) et état exact :
> `docs/PROTOCOLE_V2.md`.

1. Un paramètre de TaskRun pour la température de base (T0 explicite, tous bras) — infrastructure de mesure.
2. Le bras C : une stratégie qui compose le texte du §2 à partir des seules informations autorisées (nouvelle
   stratégie : à approuver, puisque la stratégie d'apprentissage ne doit pas encore être modifiée).
3. Les 10 tâches nouvelles et leur validation ; la phase de calibration.
4. Le script A/B/C (carré latin, McNemar + Holm, règles d'arrêt), sur le modèle de `scripts/apex_experience_ab.py`.
