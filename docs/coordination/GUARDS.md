# Coordination checks: implementation and activation

This extends WORKFLOW.md and the Issue #3/#4 ledger; it does not replace them.
Inference contract on #4 is still a proposal. No runtime or model change is included.

## Isolated environments and checkpoints

Codex uses a native Windows clone with its own `.git`, index and branch
`agent/codex/4-inference-diagnostics`. Claude keeps its WSL working tree separate.
Do not open the WSL clone from Windows or reuse either agent's index. Before work:

```text
git status --short --branch
git remote -v
git worktree list
git fetch origin
python scripts/coordination_guard.py sync
```

Use Python 3.12+ (Windows: `python`, WSL: `python3`). The sync command reads GitHub
Issues, comments on #3/#4, PRs and integration HEAD; it does not write or fetch.
Provide a minimally scoped read-only `GH_TOKEN` through the environment, never a
committed file or command-line argument. It requires network access. Git fetch
uses the normal Git credential manager. No daemon or instant notification is
provided. Repeat checkpoints before substantial edits, after contract changes,
before review and immediately before human integration. Publish progress in
Issues and push the task branch normally. A snapshot is not a distributed lock.

## Central policy and trust boundary

`ownership.json` version 1 is a proposed initial policy based on actual modules.
Claude's runtime zone is reserved, but its precise Issue #3 write-set is empty
until Claude declares it and a human authorizes it. Unassigned files are denied;
there is no permissive wildcard. Existing tests are not silently assigned.
Codex diagnostics files are reserved; shared types, errors, config and loading
dispatch are shared. Governance includes the guard, tests, registry, workflows
and existing coordination instructions. No agent may edit governance by merely
adding a path to its PR body or modifying the head version of the registry.

The guard reads **policy from current integration**, and CI runs **guard code
from integration**, not the PR head. Both source and destination of renames are
checked. Unknown/overlapping zones, wrong task branches, out-of-scope edits and
branches not containing current integration fail. Read access remains unrestricted.

`check --base <full-SHA> --head <full-SHA> --branch <task-branch>` checks scope and
ancestry only. It is not merge permission. Base must be the current integration
SHA; supplying an old base locally does not prove freshness. `pr --number N`
reads current refs, readiness, mergeability and reviews from GitHub and rechecks
refs for races. Fetch integration and PR objects before running it. API/Git errors,
unknown mergeability and incomplete pagination fail closed.

Independent reviewers must be configured in the base policy's `human_reviewers`.
It intentionally starts empty: all merge readiness checks remain BLOCKED. The
shared account `chadown62-glitch` is excluded even if accidentally allowlisted.
Only an APPROVED review from an allowlisted **different human account**, on the
exact PR head, satisfies this gate; bot, self, stale, revoked and changes-requested
reviews do not. This is independent human review, not automatic Claude/Codex
cross-review. Each agent must additionally record its technical cross-review in
GitHub, as WORKFLOW.md requires. The shared account cannot cryptographically prove
which agent posted a comment or pushed a branch; separate credentials/accounts
are needed for enforceable agent identity. An administrator with bypass can still
defeat repository controls. Do not claim otherwise.

## Exceptions, transfers and shared contracts

A normal approval alone never grants an out-of-scope exception. An independent
human reviewer configured in the trusted base must APPROVE the current head with
one exact line per authorized file in the review body:

```text
APEX-AUTHORIZE: apex/models/model.py
APEX-AUTHORIZE: docs/coordination/ownership.json
```

No globs, directory permissions or PR-body declarations are accepted. Authorization
binds exact files to the reviewed head SHA; pushing invalidates it. Changes
requested by any reviewer still block readiness. Such exceptions never bypass
freshness, invalid paths, branch registration or mergeability.

Ownership transfer or a new task is a separate registry PR: publish its Issue,
reason, previous/new owner, exact paths, shared-contract version and tests, obtain
the other agent's technical review and explicit independent human authorization
of `ownership.json` and every other governed file changed. The old BASE policy
checks that PR; the new policy applies only after human integration. Future
specialist tasks can be registered this way using real modules chosen when their
mission is assigned. No specialist model or new loading architecture is created.

## CI bootstrap and real protection: NOT ACTIVE YET

This PR creates a guard absent from integration; it cannot authorize its own
bootstrap. The bootstrap requires an explicit human review and integration
decision. CI adversarial tests run via `pull_request` with read-only permissions,
no secrets, no cache, no persisted credentials. A green test run alone is not an
ownership/merge authorization: head code can alter its own tests.

The trusted guard workflow uses `pull_request_target` or manual dispatch, checks
out only integration and fetches PR objects as data without executing PR code.
GitHub sources `pull_request_target` workflows from the default branch; currently
the default is `main`. This workflow is not deployed there, and this mission does
not alter main. Therefore automatic guard execution is NOT claimed. After human
bootstrap, a manual dispatch on integration may be used where supported, but must
be verified against an actual run. Review changes/integration movements do not
trigger automatic reruns in this draft: rerun the guard before readiness.

Activation requires an authorized administrator, after reviewing the concrete PR:

1. Choose an approved trusted workflow deployment: a separately authorized default
   branch migration, or an approved default-branch workflow deployment. Neither is
   performed here. Verify Actions event policy allows this event. GitHub documents
   forthcoming enforcement for public repositories; do not disable policy blindly.
2. Configure independent human reviewer identities and Claude's exact write-set in
   a reviewed governance PR. The agents must not configure their own approval keys.
3. Protect integration with required PR, independent reviews, stale-approval
   dismissal, most-recent-push review, strict up-to-date checks, no force push or
   deletion and no admin/actor bypass. Require relevant runtime tests as well.
4. Require the unique trusted coordination guard check bound to its expected
   GitHub App/source. Verify its status attaches to the reviewed PR head, including
   manual-dispatch runs; if it does not, an authorized trusted status publisher or
   required-workflow ruleset is needed. Do not treat a dispatch-only run as a gate.
5. Inspect PR checks/check runs for actual failure/success and billing/permission
   errors. Run controlled negative PRs before declaring protection operational.

Repo Settings, Actions policies and branch rules were not changed. Initial API
audit showed integration `protected=false` and no rulesets. Until bootstrap,
independent identities, required checks and no-bypass rules are all verified,
this is tested detection plus a proposed CI gate, not enforced locking.

Official references:
- https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches

## Evidence and recovery

Run `python -m unittest discover -s tests -p test_coordination_guard.py -v`.
The tests use isolated temporary Git repositories and synthetic GitHub responses,
clearly distinct from real GitHub enforcement or inference evidence. They cover
both directions of owner violations, shared/governance edits, head-policy rewrite,
rename laundering, stale branch, missing/self/bot/stale/revoked review, review
revocation and false/unknown mergeability. No real agent work is modified.

Windows validation: Python 3.12.14, Git 2.55.0; 15 tests passed. Initial sandbox
run denied temporary Git directories; the rerun with authorized access passed.
Linux and GitHub Actions evidence must come from actual CI, not this local result.
Rollback of the policy requires another governed PR with human authorization;
never delete checks or loosen protection silently to make a blocked PR green.
