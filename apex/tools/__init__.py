"""Tool Engine d'APEX (06/10/2026) — outils réels, bornés à un espace de travail, exécutés par le runtime contrôlé.

`model` (contrat d'un outil, permissions, erreurs structurées, résultat, provenance), `schema` (validation des
arguments), `workspace` (espace de travail fail-closed), `engine` (registre et exécution contrôlée), `builtin`
(outils fichiers, inspection, exécution de code et tests dans la sandbox), `sandbox_runner` (pont vers
`AgentRuntime.execute_in_sandbox`). Volontairement vide d'imports.
"""
