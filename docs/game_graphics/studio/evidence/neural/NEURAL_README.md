# Mission #14 — véritable TripoSR CPU, comparaison 64/128

Deux inférences réelles ont été exécutées, sans CUDA, sans téléchargement d'un second modèle et sans entraînement. Les deux meshes proviennent du checkpoint TripoSR MIT et de la même image de fauteuil créée dans Blender par APEX. Le fauteuil de référence procédural est une entrée de reconstruction ; les meshes TripoSR sont des sorties neuronales distinctes, identifiées comme telles.

## Mesures réelles

| Grille d'extraction | Temps enfant surveillé | Forward neuronal | Extraction | RSS maximal échantillonné | Sommets / triangles source |
|---|---:|---:|---:|---:|---:|
|64³|22,65 s|13,14 s|0,589 s|3 741 827 072octets|2 375 / 4 716|
|128³|25,66 s|13,52 s|3,579 s|3 729 080 320octets|9 711 / 19 360|

Quatre threads CPU ; cgroup 4 CPU et 32 Gio RAM ; pas de périphérique NVIDIA/CUDA. Limites identiques 720 s et 20 Gio RSS, seuil densité 25 et chunks 4096. Le maximum Linux getrusage distinct est conservé dans chaque inference.json ; le pic échantillonné peut être légèrement inférieur. Les deux processus se sont arrêtés normalement et le verrou APEX partagé a été libéré.

Le SHA du PNG prétraité est identique (`ad83c9399d5b69e504a3c409a1e3ed93442f63d60b2860dda788e870d43ca2d2`), comme celui des codes neuronaux (`90f74496bd267b79722de140b1c5fae9fef3f64e7c4a6cb3fcd297776a1819e1`). La comparaison isole donc la densité d'extraction du même champ neuronal.

## Preuves et limites visuelles

Chaque résultat a été importé réellement dans Blender 4.3.2, sauvegardé en neural.blend, rendu depuis quatre vues 960×720 avec Cycles CPU 64 samples, seed 14, puis rouvert par un deuxième processus avec inspect_vehicle.py PR12 inchangé. Caméras, éclairages et normalisation d'affichage de 64 sont gelés pour 128. Le débruitage reste désactivé car un premier appel réel a établi l'absence d'OpenImageDenoise ; ce premier échec est conservé.

Rendus 64 :88,59 s, RSS 315 252 736 octets ; réouverture 1,26 s.
Rendus 128 :90,63 s, RSS 314 769 408 octets ; réouverture 1,26 s.

L'amélioration géométrique est visible : les accoudoirs et pieds fragmentés à 64 sont reliés à 128 ; les contours sont plus fins. Le nombre de composantes géométriques passe de 3 à 1. Cela ne constitue pas une acceptation de qualité : facettes/stries, couleurs affichées trop claires et défauts topologiques subsistent. Aucune réparation de géométrie n'a été effectuée par le script de rendu ; les sources GLB/PLY restent inchangées.

La conversion native glTF vers Blender valide les faces : à 128, quatre triangles aux indices dégénérés sont éliminés par l'importeur (19 360 source contre 19 356 triangles dans Blender). Cette différence est explicitement conservée, pas masquée comme une réparation autonome réussie.

| Inspection |64 brut|128 brut|64 copie soudée diagnostic|128 copie soudée diagnostic|
|---|---:|---:|---:|---:|
|Arêtes frontière|68|148|0|0|
|Arêtes incidentes à>2faces|0|4|0|0|
|Composantes|3|1|3|1|
|Faces aire<1e-10|0|6|0|1|

Les copies diagnostiques sont soudées en mémoire à distance euclidienne 1e-6, sans sauvegarde. Elles ne remplacent pas les meshes livrés. Le juge PR12 soude également une copie BMesh avant ses mesures ; il rapporte à 128 une face dégénérée persistante. Le passage à 128 ne doit donc pas obtenir une acceptation Soigné par simple diminution du nombre de composantes.

## Pourquoi les rendus paraissent blancs

Les couleurs réellement générées ne sont pas majoritairement blanches : à 128, moyenne RGB8=(133,20;97,58;76,91), médiane=(153;107;79), aucun sommet dont les trois canaux dépassent 200. La première intégration avait aussi omis un matériau : Trimesh exporte COLOR_0 sans material slot, Blender rend alors blanc. Un matériau neutre connecté aux couleurs importées a été ajouté ; les premiers rendus blancs sont archivés et exclus des preuves de qualité neuronale.

L'aspect encore clair peut provenir du transfert RGB affichage→RGB linéaire : le prédicteur donne des couleurs liées aux images d'entraînement, Trimesh les écrit directement en COLOR_0 normalisé, et Blender les importe comme couleurs linéaires. Éclairage et AgX ajoutent des facteurs confondants. C'est une hypothèse étayée par le code et les statistiques, pas un diagnostic colorimétrique qualifié.

Prochaine expérience prioritaire : conserver le mesh 128 et ses caméras, comparer un rendu non éclairé/émission avec transfert direct contre décodage exact sRGB→linéaire, vue Standard et exposition 0, d'abord sur des plages RGB connues. Aucun nouveau poids ni forward neuronal nécessaire. Cette correction serait une correction de l'affichage des couleurs, jamais une amélioration de la géométrie. Ensuite, utiliser une seconde famille tenue à l'écart avec les critères 128 figés pour mesurer la généralisation.

## Provenance et adaptations

- TripoSR : code MIT `107cefdc244c39106fa830359024f6a2f1c78871`.
- Modèle public : `stabilityai/TripoSR`, révision `5b521936b01fbe1890f6f9baed0254ab6351c04a`,1 677 246 742 octets, SHA256 `429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee`.
- Configuration DINO Apache-2.0 épinglée :454 octets, aucun deuxième poids téléchargé.
- torchmcubes MPL-2.0 : source `879926d0ef58e6ce0ac2630fdecb5e53af7ed3ff`, compilation C++ CPU effective, aucune modification amont.
- `triposr-local-cpu.patch` : import rembg facultatif ; JSON DINO local épinglé ; `torch.load(weights_only=True)`. Réseau neuronal et poids inchangés.
- PyTorch 2.14.1+cpu et Transformers 4.57.6 existants ; Trimesh 4.8.3 local après échec réel de 4.0.5 sous NumPy 2.5.4 ; dépendances locales, venv partagé intact.

## Intégration portable

Les fichiers `portable_neural.py` et `portable_neural_cpu.py` sont destinés respectivement à `apex/game_graphics/studio/neural.py` et `scripts/game_graphics/studio/neural_cpu.py`. Ils n'embarquent aucune path /workspace. Le wrapper fixe le worker, vérifie le commit Git de TripoSR, les SHA des 11 fichiers Python amont adaptés, le checkpoint, les deux configurations et refuse une sortie existante. Il réutilise resource_lease, surveille l'enfant et conserve les preuves. Les répertoires de dépendances sont des installations de confiance configurées par l'opérateur, pas du code produit par le LLM ; ce mode local n'est pas une sandbox système.

Interface : `python -m apex.game_graphics.studio.neural --source <clone-triposr> --weights <model.ckpt> --config <config.yaml> --dino-config <dino-config.json> --deps <deps> <torchmcubes-source> <torchmcubes-build-cxx> --input <reference.png> --output <nouveau-dossier> --runtime <runtime-apex> --resolution 128 --timeout 720 --rss 20480`.

Le lanceur portable intégré au dépôt a ensuite réellement exécuté une troisième inférence128 :27,94s, pic RSS échantillonné3 746 381 824octets, processus arrêté. Les codes neuronaux sont SHA-identiques. Le GLB binaire diffère par l'ordre des sommets/faces émis par marching cubes CPU ; le multiensemble canonique des triangles, les positions (distance maximale0) et les couleurs associées sont identiques. Voir `portable-verification/resources.json` et `mesh-reproduction.json`. Cette vérification reproduit le même résultat, sans nouvel entraînement ni nouveau modèle.

## Fichiers

- `audit.json` : critères et provenance préparés avant tout essai.
- `chair-cpu-64-summary.json`, `chair-cpu-64-resource.json`, `chair-cpu-128-resource.json` : mesures originales.
- `comparison64-128.json` : comparaison et identité des entrées/codes.
- `topology-and-colour-analysis.json` : mesures brutes, copies diagnostiques et hypothèse colorimétrique.
- `visual-inspection/` et `visual-inspection-128/` : scripts réellement exécutés, logs, projets, quatre vues natives, inspection PR12 indépendante, SHA.
- Répertoire runtime `neural14-research/chair-cpu-64/` et `chair-cpu-128/` : GLB, PLY, codes du réseau, PNG d'entrée et rapports d'inférence.
