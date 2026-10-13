# Préparer une adaptation du contrôleur

Aucun entraînement n'est exécuté et aucun poids APEX/Qwen n'est modifié dans ce lot. Le gain vient des outils Blender, des plans d'opérations, de l'inspection et de corrections vérifiées.

Le dataset conserve les briefs originaux autorisés, méthodes, SHA des fichiers et du jugement, réponses Qwen brutes, plans validés, corrections et révisions rejetées. Les familles fixent les partitions : véhicule `TRAIN`, bâtiment `VALIDATION`, fauteuil `HELDOUT`. Toutes les versions d'un asset restent dans la même partition. La partition validation est portée par le dataset ; le modèle de tâche APEX existant n'est pas étendu.

Les exemples acceptés TRAIN sont candidats à l'apprentissage du catalogue technique. Un PASS ne devient jamais une étiquette « beau » ou « photoréaliste ». Les fichiers rejetés sont conservés pour l'audit et exclus de l'entraînement. Les exemples neuronaux doivent garder source, révision, licence et SHA des poids ; ils ne sont pas reclassés en créations procédurales.

Les minimums préétablis sont 100 exemples TRAIN acceptés, 10 familles, 30 VALIDATION indépendants et 30 HELDOUT indépendants. Plusieurs versions du même fauteuil ne sont pas des tâches indépendantes. Ce corpus est très inférieur à ces minimums : entraînement **BLOCKED**. La famille fauteuil a été observée pendant le débogage du contrôleur ; son split HELDOUT est conservé, mais aucun exemple de ce lot ne compte comme évaluation aveugle indépendante. Réserver une nouvelle famille non exposée après gel des outils/prompts.

Une expérience LoRA/QLoRA utile viserait les poids de base Qwen2.5-Coder-7B-Instruct, sous Apache-2.0, puis comparerait le contrôleur adapté au modèle initial sur des familles jamais entraînées. Le GGUF Q5 utilisé pour l'inférence n'est pas directement le checkpoint d'entraînement. Il faudrait mesurer validité des plans, corrections réussies sans régression, budget de calcul et généralisation artistique, avec juges indépendants.

QLoRA 4 bits avec petits lots, accumulation et checkpointing est une piste CUDA à mesurer ; une RTX de 8 Go ne constitue pas une preuve que 7B y sera entraînable avec le contexte nécessaire. En CPU, un fine-tuning 7B n'est pas justifié ici. Avant d'acquérir de nouveaux poids, enrichir les familles et établir la baseline indépendante est l'expérience prioritaire. Aucun calendrier ni gain de qualité des poids n'est annoncé sans mesure.
