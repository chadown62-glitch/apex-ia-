# ADR-0013 — GATE-14 bloque la phase 21, plus la phase 3

**Statut :** Acceptée — décision humaine, pré-phase 3

## Contexte

ADR-0012 a réconcilié GATE-19 avec `apex.sandbox.detect.detect_capabilities()`
et laissé GATE-14 (« Virtualisation et cgroups ») inchangé, en notant que
GATE-14 mesure une capacité distincte — cgroups v2 unifié, KVM, virtualisation
imbriquée — pertinente pour l'infrastructure Docker/gVisor de durcissement,
pas pour la capacité propre de `SandboxProvider`. Cette ADR tranche
explicitement une question laissée ouverte par ADR-0012 : à quelle phase
GATE-14 doit-il être rattaché ?

`docs/COGNITIVE_ARCHITECTURE.md` §15 est explicite : **Phase 3 = Sandbox**,
« exécuter du code généré sans risque pour l'hôte ». Ce cœur fonctionnel est
déjà construit et validé (`LinuxNamespaceSandbox`, niveau NAMESPACED,
namespaces + cgroups v1) — GATE-19 le mesure et le rapporte honnêtement
depuis ADR-0012. `docs/adr/0005-plafond-isolation-wsl2.md`, elle, relie
explicitement KVM/virtualisation imbriquée à une future « VM dédiée »
d'isolation renforcée pour le Red Team, une capacité de la **phase 21**, pas
de la phase 3.

`blocks_phases=(3,)` sur GATE-14 était donc une étiquette héritée de la
Phase 1, écrite avant que NAMESPACED n'existe et avant que GATE-19 ne le
reconnaisse. Techniquement, ce champ est purement informationnel :
`apex.gate.report.EnvironmentReport.blocking`/`status` ne se fondent que sur
`GateStatus` (FAIL), jamais sur `blocks_phases` — corriger cette étiquette ne
change donc aucun calcul de blocage réel, seulement l'exactitude de ce que
GATE-14 prétend bloquer.

## Décision

**GATE-14 déclare désormais `blocks_phases=(21,)`, plus `(3,)`.** Sa sévérité
et sa logique de détection ne changent PAS : cgroups v2 absent reste `FAIL`
sur une machine qui ne l'a pas, honnêtement — aucune rétrogradation FAIL→PASS
pour obtenir un déblocage de phase. Seule l'étiquette de phase corrigée
reflète la réalité : GATE-14 conditionne l'infrastructure d'isolation
renforcée du Red Team (phase 21), pas le cœur fonctionnel de la phase 3.

Le message et la remédiation de GATE-14 sont également corrigés : l'ancien
texte (« les limites CPU, mémoire et pids de la sandbox ne seraient pas
appliquées ») était devenu factuellement inexact depuis ADR-0012 — ces
limites SONT appliquées, via cgroups v1, par `LinuxNamespaceSandbox`, mesuré
et rapporté par GATE-19. GATE-14 ne mesure que l'absence du chemin v2
unifié, jamais l'absence de toute limite de ressources.

## Conséquence — deux niveaux qui ne doivent jamais être confondus

1. **Le Gate d'environnement** (`apex gate`) : un relevé de ce que CETTE
   machine offre, à cet instant. GATE-06 (Docker) et GATE-14 (cgroups v2)
   restent honnêtement `FAIL` sur une machine qui ne les a pas — ce n'est ni
   une régression ni un dysfonctionnement du code.
2. **La validation fonctionnelle** (tests, `apex.sandbox`) : la phase 3
   (Sandbox) est déjà construite et prouvée par des tests d'intégration
   noyau réels, indépendamment de l'état de GATE-06/14 sur telle ou telle
   machine. Un Gate global `FAIL` documente une limitation d'environnement ;
   il ne signifie pas que le code de la phase 3 est cassé ou non fonctionnel.

Le Gate global de cette machine reste `FAIL` après cette ADR — cette
correction ne change ni GATE-06 ni la sévérité de GATE-14, et ne cherche pas
à produire `READY_FOR_PHASE_3` artificiellement.

## Écarté

Assouplir la sévérité de GATE-14 (FAIL→DEGRADED/PASS) pour obtenir un Gate
global vert : rejeté explicitement — ce serait exactement la rétrogradation
que le principe verrouillé n°4 (la sécurité n'est pas un curseur) interdit,
et cgroups v2 reste réellement absent sur cette machine.

Fusionner GATE-14 dans GATE-19 : rejeté — déjà écarté par ADR-0012 pour les
mêmes raisons (capacités distinctes, changement d'architecture plus large que
nécessaire pour cette correction).
