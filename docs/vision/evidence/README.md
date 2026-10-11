# Provenance des preuves vision

Les documents de ces preuves sont créés par `scripts/vision/build_fixtures.py`.
Ils ne contiennent aucune donnée utilisateur. Les images sont synthétiques,
même lorsque le JPEG imprimé est déformé ; ce ne sont pas des photographies de
téléphone. Les réponses attendues sont réservées aux assertions et ne sont pas
envoyées au modèle.

Toutes les sorties préentraînées proviennent de Qwen2.5-VL-3B-Instruct,
révision `66285546d2b821cf421d4f5eb2576359d3770cd3`, avec poids locaux vérifiés,
PyTorch CPU/BF16 et aucun appel de service IA. Les JSON de résultat incluent
les empreintes image/poids, les tokens, temps et RSS instantanés. Les fichiers
`.txt` sont les générations réelles observées avant leur validation ; aucune
réponse n'est reconstruite à partir d'une erreur.

| Fichier | Sens de la preuve |
| --- | --- |
| `exercise-initial-failure.txt` | Première génération : équation lue, résultat faux `5`, clé invalide ; échec. |
| `exercise-intermediate.json` | Après JSON contraint : `4`, calcul vérifié, libellés visuels génériques encore présents. |
| `document-length-failure.*` | Répétitions jusqu'à 768 tokens, JSON inachevé ; `invalid_output`, aucun résultat accepté. |
| `document-bounded.*` | Décodage borné : cellules et résultat `3 g/cm3` corrects, texte pédagogique dégradé ; qualité partielle. |
| `exercise-bounded.*` | Étapes aboutissant à `x = 4`, mais réponse finale `null` et ambiguïté inventée ; assertion sémantique échouée. |
| `chart-bounded.*` | Barres A=2/B=5 et différence 3 lues, mais réponse finale `null`, texte visible omis et ambiguïtés inventées ; échec. |
| `blank-bounded.json` | CLI réelle, abstention correcte, texte/éléments vides ; phrases encore coupées, ArtifactStore relu avec intégrité vérifiée. |
| `validation-summary.json` | Résumé des 46 contrôles contrat/OCR et quatre inférences réelles : deux PASS minimaux, deux FAIL ; relecture séparée. |

Un statut `complete` et une assertion numérique réussie ne garantissent ni
fidélité de chaque phrase, ni correction universelle. Les dérives, incertitudes
et champs saturés restent visibles. Le rapport parent détaille la portée des
contrôles minimaux et de la relecture, ainsi que les limites matérielles.
