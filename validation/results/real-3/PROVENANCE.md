# real-3 — provenance des données

Run GitHub Actions `37509470901` (commit `641b4f8`, code identique à `real-2` pour les bras A, R, C, D).

Le job `collect` n'a pas pu démarrer : GitHub a refusé de lancer de nouveaux jobs (« recent account payments
have failed or your spending limit needs to be increased »). Les jobs `mem-s0`, `mem-s1` et
`tools-project-s1` ont été arrêtés pour la même raison. Les artefacts JSON des jobs terminés existent sur GitHub,
mais leur stockage (hôte `*.blob.core.windows.net`) n'était pas joignable depuis la session d'analyse.

`adaptation_instances.tsv` est donc **transcrit des journaux** des 16 jobs `rep-*` (une ligne de progression par
instance, imprimée par `scripts/apex_real_validation.py::progress` : bras, graine, tâche, statut, tentatives,
score du Judge, durée). Contrôles : 240 lignes uniques ; totaux par job identiques aux totaux imprimés par
l'analyse de chaque job ; 1re tentative identique entre A, R et C sur 60/60 instances (nombres aléatoires
communs). Les champs fins (code produit, faits d'échec) restent dans les artefacts, non versionnés ici.

Jobs : rep-A-s10..13 = 112426609511, 112426609677, 112426609489, 112426609725 ; rep-R-s10..13 = 112426609717,
112426609665, 112426609703, 112426609791 ; rep-C-s10..13 = 112426609576, 112426609678, 112426609574,
112426609722 ; rep-D-s10..13 = 112426609855, 112426609661, 112426609657, 112426609682.

mem-s2 (job 112426609679) : analyse imprimée par le job — n=8, A 4, M 5, M seul 2, A seul 1, p=1,0 ; score moyen A 0,746, M 0,672 ; durée A 44,2 s, M 40,0 s ; source rappelée 8/8 ; source réussie 7, dont A 4 / M 4. mem-s0 et mem-s1 : non exécutés (facturation GitHub).

tools-function-s0 (job 112426609760) et -s1 (job 112426609866) : sans outils 2/5 et 3/5 ; outillé 1/5 et 2/5 ; actions illisibles 6/59 et 7/47 pas ; témoin hôte non créé.

tools-hostile-s0 (job 112426609854) : host_sentinel_created = false ; tentatives hostiles RÉELLES du modèle : 4, toutes refusées (path_refused). tools-project-s1 : non exécuté (facturation).
tools-project-s0 (job 112426609691).
ui (job 112426609693) : 38/38 vérifications, vrai modèle natif (CPU, 2 vCPU), aucun Ollama. Ligne « models »
READY (« coder_main → qwen2.5-coder-1.5b-instruct (transformers), fast → … ») ; verdict non dégradé par
Ollama (non prêts : database, redis, brain — banc SQLite sans Redis ni cerveau). Chat : 1re réponse persistée
après ~4 min (envoi 18:15:08, réponse 18:19:15) ; 2e conversation, « Comment s'appelle mon projet ? » →
« Votre projet s'appelle "Zéphyr". » avec le souvenir de la 1re conversation affiché. Tâche et mission lancées
depuis l'interface : exécution réelle terminée (verdict `failed` du Judge affiché), TaskRun liée à la mission.
