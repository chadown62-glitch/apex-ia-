# APEX — dual-agent coordination protocol

## Current migration context
- Source baseline for new work: `apex/integration`, initially branched from Claude's existing work at `fab616df063cb222cba6355701409df649212650` (2026-10-11).
- Existing `main` has independently diverged: as observed at setup, integration-source was 337 commits ahead and 11 behind main. This is NOT automatically resolved; preserve both lines and reconcile separately if/when authorized.
- Existing PR #2 targets `main`; do not auto-merge it to bootstrap multi-agent work.

## Work allocation ledger: GitHub Issues
Create one Issue per independent deliverable, with this template in its body or initial comment:

```text
Owner: Claude | Codex
State: PROPOSED | CLAIMED | ACTIVE | BLOCKED | REVIEW | DONE
Branch: agent/<owner>/<issue>-<slug>
Base integration SHA: ...
Write-set (files/directories): ...
Read-only dependencies / contracts: ...
Potential overlaps: ...
Acceptance criteria / tests: ...
Latest progress and blockers: ...
```

Never assign two owners the same write-set at the same time. Shared interfaces should be agreed in Issues before implementation; use an explicit interface owner or a prerequisite PR. Ownership is advisory until safeguarded by PR checks and human review.

## Operating loop
1. Every agent independently fetches the latest integration ref, lists open Issues and PRs and reads the latest comments on relevant work.
2. Declare file ownership and planned changes; cross-check against the other active Issue before editing.
3. Work in separate worktrees. Issue comments reflect checkpoints (start, unexpected scope, contract changes, blocked, ready for review).
4. Push a draft PR early if collaboration requires visibility into exact diffs; keep changes in separate branches.
5. Agent B reviews Agent A's PR, and vice versa. Reviewers may suggest fixes but must not push changes to each other's task branches without explicit coordination.
6. Integration is sequential, gated on up-to-date diff, tests and human authorization. If `apex/integration` moves, subsequent PRs must revalidate before merge.
7. After merge, close the Issue and update linked dependencies. Other agent reloads the integration history and reruns relevant tests.

## Hard gates versus conventions
- Worktrees, naming, write-set claims, comments and cross-reviews are procedural. They DO NOT constitute distributed locks and cannot guarantee perfect real-time awareness.
- Enable branch protection/rulesets for `apex/integration` through GitHub repository Settings: require PR, required checks, no force-push, no deletion, no bypass, and preferably merge queue if available on the current plan.
- Configure CI workflows with appropriate permissions and usage budget. A failed/billing-blocked CI cannot be reported as successful.
- Review the separate `main` history before changing the default branch or closing the pre-existing PR #2.

## Safety conditions
No overlapping edits, no direct integration pushes, no force push. In case of disagreement, conflicting dependency, lost GitHub connectivity, or changed contract: mark BLOCKED and notify the user rather than silently diverging.

## Initial parallel roles (after explicit task assignment)
Claude: primary local Q5 inference and runtime integration, not redesign of the full tool/memory stack.
Codex: independent diagnostics and integration test harness / evidence reporting, not editing Claude-owned inference implementation files.
Agree on API contracts first. Scope/ownership must be concretely declared on their respective Issues.
