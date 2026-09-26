# APEX — Boucle autonome d'amélioration (Priorité 1)

## 0. Audit du code réel (avant toute ligne de code)

Point de départ : `03648ab`. Chaque constat ci-dessous a été vérifié dans le
code (lecture + recherche des appelants), pas dans la documentation.

### Ce qui existe et sera RÉUTILISÉ tel quel

| Composant | Où | Rôle dans la boucle |
|---|---|---|
| Modèle, tokenizer, moteur d'entraînement, checkpoints | `apex/neural/{model,tokenizer,training,checkpoint}.py` | exécution réelle d'une expérience (forward, loss, backward, optimizer) ; reprise bit-exacte |
| Dataset (provenance, dédup canonique, fuite par document, empreinte) | `apex/neural/dataset.py` | monde gelé : train / validation / held-out |
| Knowledge Agent | `apex/knowledge/` | construction des données réelles du monde |
| Évaluation BPB par document + `NeuralJudge` (StatTest apparié) | `apex/neural/evaluation.py` | cœur statistique du Judge indépendant |
| Registre des versions (chaîné, promotion sur ACCEPT) | `apex/neural/registry.py` | promotion ; à étendre avec le retour arrière |
| ArtifactStore local adressé par contenu | `apex/artifacts/local_filesystem.py` | conservation vérifiable des checkpoints (champion, candidats) |
| StatTest | `apex/stattest/` | via `NeuralJudge` |

### Ce qui existe mais ne convient PAS (et pourquoi)

| Composant | Constat | Décision |
|---|---|---|
| `apex/weakness` (`WeaknessDetector`) | classe des `EvalRun` (PASS/FAIL de tâches de code) ; aucun lien avec le modèle neuronal | non réutilisé ; même discipline (jamais de faiblesse sans preuve citable) |
| `apex/hypothesis` (`Hypothesis`, `HypothesisGenerator`) | hypothèse bâtie sur un `WeaknessReport` d'`EvalRun` ; sa propre docstring constate qu'« aucun consommateur réel (ExperimentEngine) n'existe » ; jamais appelé hors `taskgen` (qui le nomme) et ses tests | concept conservé (affirmation, prédiction, réfutation), type spécifique au modèle neuronal |
| `apex/metalearner/learning_orchestrator`, `autonomy`, `cycle` | boucles réelles, mais qui font évoluer des génomes/prompts de clones sur des tâches de code via `EvalRun` et une base SQL | autre objet d'apprentissage ; pas de second orchestrateur de génomes |
| `apex/improvement` (CI engine) | améliore le CODE d'APEX (mesure, juge épinglé, ledger) ; ne décide ni n'entraîne rien | réutilisé pour valider CETTE capacité (cycle 2), pas comme boucle d'apprentissage |
| `apex/memory` (`MemoryService`) | exige une base SQL ; conçu pour des connaissances (affirmations) | le journal d'expériences est la source de vérité ; export vers la mémoire traité comme extension, pas comme dépendance |
| `apex/neural/experiment.py` | script monolithique : chaîne fixe, corruption V2 codée en dur, aucun paramètre de modification | conservé pour les expériences manuelles ; la boucle a son propre exécuteur paramétré par un plan |

### Le gap réel

1. **Rien n'observe le modèle neuronal** : aucun code ne relit le champion,
   ses mesures par domaine, l'historique des expériences.
2. **Aucune hypothèse n'est jamais exécutée** : `HypothesisGenerator` n'a
   aucun consommateur ; rien ne transforme une faiblesse en expérience.
3. **Aucune expérience paramétrable** : `run_experiment` n'accepte ni
   baseline, ni modification, ni budget, ni critères.
4. **Aucune analyse d'échec pour le modèle neuronal** : un REJECT du
   `NeuralJudge` est une liste de raisons textuelles, rien ne l'exploite.
5. **Aucune stratégie adaptative** : rien ne choisit l'expérience suivante
   en fonction des précédentes.
6. **Aucune mémoire d'expériences** : les rapports JSON existent, mais rien
   ne sait « j'ai déjà essayé ça », ni pourquoi cela a échoué.
7. **Promotion sans retour arrière** : le registre ne sait que promouvoir ;
   les checkpoints ne sont conservés que par leur SHA-256 (fichiers dans un
   répertoire temporaire) : un retour au champion précédent est IMPOSSIBLE.
8. **Benchmark instable entre expériences** : chaque exécution reconstruit
   son dataset ; dès que les données changent, le Judge refuse (à raison) la
   comparaison. Une boucle a besoin d'un monde gelé : held-out, tokenizer et
   benchmarks protégés fixés au démarrage, jamais modifiables par une
   expérience.
9. **Aucune borne, aucun arrêt, aucune reprise** pour une boucle de ce type.
