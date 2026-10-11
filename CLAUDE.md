# Claude instructions for APEX

Follow `AGENTS.md` and `docs/coordination/WORKFLOW.md` before every task. These rules also apply to Codex and are designed to prevent concurrent overwrites.

Your current historic branch `claude/apex-official-development-4kao8t` is **not** the common integration branch. Preserve it unchanged unless the user explicitly requests a migration. For newly coordinated work, use a new `agent/claude/<issue>-<slug>` branch from `apex/integration` in an isolated worktree.

For each mission, first check GitHub Issues and the other agent's open PRs and announce the exact scope and file ownership. Review Codex's proposed contracts before modifying interconnected modules. If the other agent is changing shared interfaces, stop overlapping edits and negotiate the contract through GitHub.

At completion, open a PR to `apex/integration`, request Codex's review, give evidence, and do not merge your own PR without a human-authorized integration decision. Work proactively within your assigned boundaries, not on unrelated parts of the repository.
