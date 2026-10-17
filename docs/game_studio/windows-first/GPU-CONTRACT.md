# Game Studio ↔ cerveau : contrat de mise au repos

Implémentation Codex : `apex/game_studio/gpu.py`, utilisée par les mêmes services
Game Studio et les mêmes baux que précédemment. Aucun nouveau gestionnaire de
modèles. Aucun changement dans `apex/assistant/**`, `apex/models/**` ou le Panel.

Contrat lu dans PR #27, commit `1bab1b030b7d62dfc3f8bed86f02310e452914de` :
`apex.models.gpu_handoff.request_gpu(timeout_s, requester=...)`,
`return_gpu(requester=...)`, `gpu_status()`.

## Ordre obligatoire

1. Vérifier les processus externes et l'identité du profil `APEX_HOME` absolu.
2. Prendre `.apex/game-studio-handoff.lock` pour toute la durée du prêt. Ce verrou
   utilise la primitive existante `_locked` : flock POSIX / msvcrt Windows.
3. Refuser une transition ou un prêt déjà actif. Appeler le gouverneur **dans le
   processus qui possède le cerveau**. Délai maximal demandé : 30 secondes,
   comptabilisé par le budget de mission existant.
4. Exiger un ACK avec `released: true`, le même `requester`, le chemin canonique
   `.apex/model-session.lock`, `lease_held: false`, `admissions_open: false`.
   Vérifier aussi que `gpu_status().lent_to` désigne notre emprunteur.
5. Acquérir le bail commun via `game_graphics.resources.resource_lease`, puis le
   verrou propre à l'opération. Le contrôle de processus externes reste actif.
6. Exécuter les outils/modèles autorisés ; leurs contextes nettoient leurs seuls
   processus. Le gestionnaire du prêt ne tue ni ne décharge aucun modèle lui-même.
7. Libérer les verrous OS avant `return_gpu`. Ne rendre que notre propre prêt ;
   exiger `resumed: true`, notre `requester` et `lent_to` dans la réponse.
8. Sauvegarder un reçu immuable `gpu-handoff-<uuid>.json` à côté du verrou de
   l'opération, y compris après refus/échec. Les mesures CUDA restent celles du
   gouverneur ; aucun chiffre synthétique ne les remplace.

Une erreur de tâche ne supprime pas son diagnostic si le retour échoue aussi.
Une acquisition OS refusée après l'ACK rend le prêt dans `finally`. Les appels
Game Studio concurrents ne peuvent pas écraser ou rendre un prêt voisin.

## CPU explicite et garde CUDA

Une qualification **CPU explicitement choisie** peut aussi devoir obtenir le
bail : le cerveau installé tient ce même verrou lorsqu'il réside sur CUDA.
Demander sa mise au repos permet ce parcours sans annoncer une inférence GPU.

La garde `cuda_game_studio_requires_qualified_brain_quiescence_handoff` reste
inchangée aux trois points d'entrée. `auto` ne bascule pas silencieusement sur
CPU. Le simple succès de tests de contrat ne permet pas de lever cette garde.
Sans module de gouverneur installé, le parcours autonome CPU utilise le même
bail OS. Une dépendance cassée du module installé n'est pas traitée comme une
absence de cerveau.

## Point restant côté Claude

Dans le commit audité, `request_gpu` refuse `_quiescing`, mais ne refuse pas
atomiquement `_lent_to` déjà occupé. `return_gpu` ne vérifie pas atomiquement
l'identité de l'emprunteur. Le verrou Codex protège ses propres appelants ; il
ne peut pas imposer ce contrat aux autres spécialistes. Recommandation publiée
dans l'Issue #21 : refus atomique des prêts étrangers et du retour d'un autre
propriétaire **dans le gouverneur**, avec tests de concurrence.

Ne pas remplacer ce protocole par `apex_brain.unload_all()` : cela contournerait
les admissions et les inférences en cours. Ne jamais appeler un gouverneur dans
un sous-processus pour prétendre avoir libéré le cerveau d'un autre processus.

## Qualification requise sur RTX

Brain CUDA résident → inférence active → demande/attente ACK → mesures avant/après
→ bail → chargement Qwen CUDA réel → génération → arrêt vérifié du serveur et
mesure VRAM libérée → retour → nouvelle inférence du cerveau. Ajouter refus de
prêt concurrent, timeout, annulation et OOM contrôlé. La VRAM torch du cerveau
n'inclut pas le serveur llama.cpp enfant : mesurer aussi le périphérique et le
processus NVIDIA. Aucun résultat de cette séquence RTX n'est revendiqué ici.
