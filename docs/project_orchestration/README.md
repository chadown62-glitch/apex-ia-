# MVP Game Studio — Issue #8

Deux Tasks existantes liées, une Mission de gouvernance, TaskRunService/TaskLoop
pour la génération, les juges, les corrections et la reprise. Aucun nouveau
provider de modèle, scheduler, engine de tâches, table ou interface centrale.
Les rôles Game Graphics/Engineer sont des métadonnées de cette recette ; ils
ne sont pas annoncés comme enregistrés globalement. La spécialité historique
`research` enveloppe le projet et la Mission termine en REVIEW, sans promotion
humaine automatique.

Chaque sortie de TaskRun jugée complète est conservée dans l'ArtifactStore,
avec son SHA-256 et ses ids de provenance. Une reprise relit les octets et les
compare à la sortie persistée de la TaskRun. Même une perte du reçu local
retrouve les TaskRuns complétées. Les succès ne sont pas régénérés ; une étape
ne démarre pas sans artefact de son prérequis. Le budget des corrections est
transmis au TaskLoop canonique, trois tentatives par défaut.

Le gate de dépendances de Mission réutilise `_unmet_dependencies` du Scheduler
existant : dépendance externe COMPLETED obligatoire. Il s'agit d'un helper
privé de cette base, à revoir avant évolution du contrat central. Le projet
commande ses deux TaskRuns séquentiellement ; il ne crée aucune file de workers.
Un verrou OS par projet protège les reçus et les démarrages locaux. Windows
reste non testé ; ce verrou n'est pas une preuve de réservation GPU.

`AbsoluteDockerSandbox` délègue toutes les commandes/drapeaux au ContainerSandbox
existant et remplace seulement le nom du client par son chemin absolu réel.
Cela corrige l'incompatibilité PATH de ce cloud sans modifier le fichier central,
élargir l'environnement, monter l'hôte ou réduire l'isolation.

Depuis la racine, avec une venv contenant les extras `db,api,dev,native` :

```bash
python -m pytest tests/test_project_orchestration.py -ra
PYTHONPATH=. python scripts/project_orchestration/demo.py
# Par défaut : serveur local absent -> BLOCKED, code 2, pas de résultat inventé.
PYTHONPATH=. python scripts/project_orchestration/demo.py --endpoint http://127.0.0.1:8082/v1
# Reprendre l'id retourné, avec la même base privée :
PYTHONPATH=. python scripts/project_orchestration/demo.py --project-id UUID --endpoint http://127.0.0.1:8082/v1
```

La CLI utilise exclusivement le ModelProvider existant. Elle ne démarre ni
ne télécharge un modèle. Le service de modèle est une dépendance locale réelle.
La base de la CLI est privée ; sa réconciliation ne doit pas viser la base
d'un autre runtime actif. Les deux tâches de démonstration produisent un contrat
de dimensions en code Python puis une conversion mètres/centimètres, pas un mesh
ou un build UE5. Blender et UE5 relèvent des lots #9/#10.
