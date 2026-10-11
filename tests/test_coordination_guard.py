"""Adversarial checks in temporary repositories; never touch agent worktrees."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "coordination_guard", Path(__file__).resolve().parents[1] / "scripts/coordination_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / guard.POLICY).read_text())
CODEX = "agent/codex/4-inference-diagnostics"
CLAUDE = "agent/claude/3-q5-runtime"


class ScopeTests(unittest.TestCase):
    def test_codex_cannot_change_claude(self):
        self.assertTrue(guard.violations(POLICY, CODEX, ["apex/brain/inference.py"]))

    def test_claude_cannot_change_codex(self):
        self.assertTrue(guard.violations(POLICY, CLAUDE, ["scripts/inference_diagnostics.py"]))

    def test_shared_and_policy_and_workflow_are_protected(self):
        for path in ["apex/models/model.py", guard.POLICY, ".github/workflows/coordination-guard.yml",
                     "scripts/coordination_guard.py", "tests/test_coordination_guard.py"]:
            with self.subTest(path=path):
                self.assertTrue(guard.violations(POLICY, CODEX, [path]))

    def test_owned_file_allowed(self):
        self.assertEqual(guard.violations(POLICY, CODEX, ["scripts/inference_diagnostics.py"]), [])

    def test_unassigned_unknown_branch_traversal_and_prefix_collision(self):
        for path in ["apex/taskloop/loop.py", "../scripts/inference_diagnostics.py",
                     "scripts/inference_diagnostics.py.evil"]:
            self.assertTrue(guard.violations(POLICY, CODEX, [path]))
        self.assertTrue(guard.violations(POLICY, "main", []))

    def test_ambiguous_owner_denied(self):
        policy = json.loads(json.dumps(POLICY))
        policy["zones"].append({"owner": "codex", "paths": ["apex/brain/"]})
        self.assertTrue(guard.violations(policy, CODEX, ["apex/brain/inference.py"]))


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.email", "test@example.invalid")
        self.run_git("config", "user.name", "Test fixture")
        self.write(guard.POLICY, json.dumps(POLICY))
        self.write("apex/brain/inference.py", "fixture\n")
        self.commit()
        self.base = self.run_git("rev-parse", "HEAD").strip()

    def run_git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root).decode()

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.run_git("add", ".")
        self.run_git("commit", "-qm", "fixture")

    def check(self, base=None):
        return guard.check(base or self.base, self.run_git("rev-parse", "HEAD").strip(), CODEX,
                           str(self.root))

    def test_head_cannot_rewrite_its_own_policy(self):
        policy = json.loads(json.dumps(POLICY))
        policy["zones"] = [{"owner": "codex", "paths": ["apex/brain/", guard.POLICY]}]
        policy["tasks"][CODEX]["write_set"] = ["apex/brain/", guard.POLICY]
        self.write(guard.POLICY, json.dumps(policy))
        self.write("apex/brain/inference.py", "unauthorized\n")
        self.commit()
        errors = self.check()["errors"]
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(e.startswith("PROTECTED_OWNER") for e in errors))

    def test_rename_cannot_launder_source_ownership(self):
        self.run_git("mv", "apex/brain/inference.py", "scripts-copy.py")
        self.commit()
        report = self.check()
        self.assertIn("apex/brain/inference.py", report["paths"])
        self.assertEqual(len(report["errors"]), 2)

    def test_stale_branch_denied(self):
        self.run_git("checkout", "-qb", "integration")
        self.write("integration.txt", "new base")
        self.commit()
        base = self.run_git("rev-parse", "HEAD").strip()
        self.run_git("checkout", "-q", "--detach", self.base)
        self.write("scripts/inference_diagnostics.py", "fixture")
        self.commit()
        self.assertTrue(any(e.startswith("STALE") for e in self.check(base)["errors"]))

    def test_fresh_owned_change_allowed(self):
        self.write("scripts/inference_diagnostics.py", "fixture")
        self.commit()
        self.assertEqual(self.check()["errors"], [])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.policy = {**POLICY, "human_reviewers": ["human", "chadown62-glitch"]}
        self.pr = {"user": {"login": "chadown62-glitch"}, "head": {"sha": "a" * 40}}
        self.review = {"id": 1, "user": {"login": "human", "type": "User"},
                       "state": "APPROVED", "commit_id": "a" * 40,
                       "body": "APEX-AUTHORIZE: apex/models/model.py"}

    def test_independent_current_approval(self):
        self.assertEqual(guard.review_errors(self.policy, self.pr, [self.review]), [])
        self.assertEqual(guard.authorized_paths(self.policy, self.pr, [self.review]),
                         {"apex/models/model.py"})

    def test_missing_self_bot_stale_and_revoked_reviews_denied(self):
        self.assertTrue(guard.review_errors(POLICY, self.pr, []))
        for fields in [{"commit_id": "b" * 40}, {"user": {"login": "chadown62-glitch", "type": "User"}},
                       {"user": {"login": "human", "type": "Bot"}}, {"state": "DISMISSED"}]:
            review = {**self.review, **fields}
            self.assertTrue(guard.review_errors(self.policy, self.pr, [review]))
            self.assertEqual(guard.authorized_paths(self.policy, self.pr, [review]), set())

    def test_latest_changes_requested_overrules_approval(self):
        reviews = [self.review, {**self.review, "id": 2, "state": "CHANGES_REQUESTED"}]
        self.assertTrue(guard.review_errors(self.policy, self.pr, reviews))
        self.assertEqual(guard.authorized_paths(self.policy, self.pr, reviews), set())

    def test_pr_body_cannot_grant_authorization(self):
        self.pr["body"] = self.review["body"]
        self.assertEqual(guard.authorized_paths(self.policy, self.pr, []), set())

    def test_pr_conflicts_and_unknown_mergeability_fail_closed(self):
        for mergeable in [False, None]:
            pr = {**self.pr, "base": {"ref": "apex/integration", "sha": "b" * 40},
                  "head": {"sha": "a" * 40, "ref": CODEX, "repo": {"full_name": "owner/repo"}},
                  "draft": False, "state": "open", "mergeable": mergeable}
            def api(path):
                return {"object": {"sha": "b" * 40}} if "git/ref" in path else pr
            with patch.object(guard, "api", side_effect=api), patch.object(guard, "check", return_value={"errors": []}), \
                 patch.object(guard, "load_policy", return_value=self.policy), \
                 patch.object(guard, "collection", return_value=[self.review]):
                self.assertTrue(any(e.startswith("MERGEABILITY") for e in guard.pr_check("owner/repo", 1)["errors"]))


if __name__ == "__main__":
    unittest.main()
