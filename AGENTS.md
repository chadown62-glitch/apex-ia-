# APEX — shared rules for Claude and Codex

Both coding agents MUST follow this file. Claude must also follow CLAUDE.md. The source of truth for work allocation is GitHub Issues, PRs and docs/coordination/WORKFLOW.md, NOT an agent's private chat history.

## Repository safety
- Canonical integration branch: `apex/integration`. `main` and `claude/apex-official-development-4kao8t` are legacy/current branches and must never be rewritten, force-pushed, or merged automatically.
- Never push implementation commits directly to `apex/integration` or `main`. One task = one Issue = one short-lived branch = one PR into `apex/integration`.
- Branch naming: `agent/claude/<issue-number>-<slug>` or `agent/codex/<issue-number>-<slug>`.
- Start from the current integration commit in a SEPARATE worktree or clone. Never share a working tree, branch, index, or uncommitted files between agents.
- Never use `git push --force` or discard another contributor's modifications.
- Do not assume existing `claude/apex-official-development-4kao8t` is a new task branch; coordinate its future integration separately.

## Coordination (mandatory)
1. BEFORE work, read this file, `docs/coordination/WORKFLOW.md`, current open Issues, open PRs and integration HEAD. Locate the assigned Issue and check the other agent's current ownership.
2. Publish an Issue comment describing owner, branch, base SHA, proposed files/modules (write-set), shared interfaces, dependencies, status and expected test plan. Only start modifying files after checking for overlapping write-sets.
3. If scopes overlap, STOP overlapping edits and comment on both Issues. Agree on an interface/owner and split work; one agent owns each overlapping file. An Issue comment alone is a *coordination intent*, not a GitHub-enforced lock.
4. During implementation, publish useful checkpoints on the Issue and push commits/PR drafts as appropriate so the other agent can see actual progress. Before changing scope, re-check active Issues/PRs and notify the other agent.
5. Open a PR referencing the Issue. Include tested evidence, risks, migrations, changed files, and whether the integration branch has moved.
6. Cross-review: Claude reviews Codex's PR, Codex reviews Claude's PR. Reviewers identify themselves in GitHub comments. Do not self-approve, treat an automated summary as approval, or merge while substantive review comments are unresolved.
7. Immediately before merge, rebase or merge latest integration into the task branch (without rewriting shared branches); rerun applicable tests and verify conflicts. Integrate ONE PR at a time. After a merge, all other in-flight branches re-check their base and revalidate before merging.
8. A merge requires a human-authorized integrator and relevant CI checks, if available. If CI is blocked/unavailable, report BLOCKED and request explicit human approval of the exception. Never invent CI success.

## APEX product rules
- Prioritize real APEX software, not premature hardware/protocol-v2 experiments.
- No simulated intelligence/learning in production. Test mocks are allowed only in tests.
- Preserve the existing runtime, TaskLoop, Tool Engine, Task/Workspace and memory integrations; avoid parallel implementations.
- Keep APEX independent of cloud AI services for local inference. Avoid rebuilding on Ollama-specific assumptions.
- Classify claims: IMPLEMENTED, TESTED, MEASURED, INFERRED, UNVERIFIED or BLOCKED.
- No unauthorized destructive tools, secret commits, data exfiltration, or user-device validation requests.
- Large refactors and changes to public interfaces need coordination and tests.

If GitHub is inaccessible or synchronization cannot be established, do not claim concurrent coordination. Pause conflicting work, keep local changes isolated and report the blocker.
