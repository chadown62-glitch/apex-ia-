# ADR-0005 — Plafond d'isolation sous WSL2, et ce qu'il interdit

**Statut :** Acceptée — phase 1, à réévaluer en phase 3

## Contexte

Docker Desktop pour Windows ne fournit pas de moyen supporté de déclarer un
runtime OCI alternatif tel que runsc (gVisor). Avec un Docker Engine natif dans
WSL2, runsc *peut* fonctionner — le noyau WSL2 satisfait les prérequis — mais
cette configuration n'est ni supportée ni testée en amont.

## Décision

1. Docker Engine natif dans la distribution WSL2, jamais Docker Desktop.
2. Test empirique go/no-go de runsc en phase 3, mesuré et consigné, jamais
   supposé.
3. Si runsc est inutilisable, le plafond d'isolation est Docker durci :
   non-root, `cap-drop ALL`, seccomp, rootfs en lecture seule, réseau coupé,
   cgroups v2.
4. **Le Red Team (§46) reste désactivé** tant qu'un hôte d'isolation renforcée
   n'existe pas. `apex doctor` publie le fait `red_team_allowed` et
   `IsolationUnavailable` est levée si une fonctionnalité l'exige.

## Conséquence

Une fonctionnalité risquée est différée plutôt que l'isolation abaissée. C'est
la traduction opérationnelle du principe verrouillé n°4 : la sécurité n'est pas
un curseur que l'autonomie peut déplacer.

## Écarté

Activer le Red Team sous Docker standard « en attendant » : c'est précisément
la dégradation que le principe interdit, et elle serait durable en pratique.
