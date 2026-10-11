"""Read-only coordination guard. CI must run this file from the trusted base.

No dependency on APEX, no execution/import of PR code, no GitHub mutations.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

POLICY = "docs/coordination/ownership.json"
SHA = re.compile(r"[0-9a-f]{40}\Z")


def git(*args: str, cwd: str = ".") -> bytes:
    return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE)


def changed_paths(base: str, head: str, cwd: str = ".") -> list[str]:
    # No rename detection: both deleted source and added destination are guarded.
    raw = git("diff", "--no-ext-diff", "--no-textconv", "--no-renames",
              "--name-only", "-z", base, head, "--", cwd=cwd)
    return [p.decode("utf-8", errors="strict") for p in raw.split(b"\0") if p]


def load_policy(base: str, cwd: str = ".") -> dict:
    return json.loads(git("show", f"{base}:{POLICY}", cwd=cwd))


def matches(path: str, rule: str) -> bool:
    return path.startswith(rule) if rule.endswith("/") else path == rule


def violations(policy: dict, branch: str, paths: list[str]) -> list[str]:
    if policy.get("version") != 1:
        return ["POLICY_VERSION: unsupported policy"]
    task = policy.get("tasks", {}).get(branch)
    if not task:
        return ["UNREGISTERED_BRANCH: " + branch]
    errors = []
    for path in paths:
        if "\\" in path or path.startswith("/") or ".." in path.split("/"):
            errors.append("INVALID_PATH: " + path)
            continue
        zones = [z for z in policy["zones"] if any(matches(path, r) for r in z["paths"])]
        if len(zones) != 1:
            errors.append("UNASSIGNED_OR_AMBIGUOUS: " + path)
        elif zones[0]["owner"] != task["owner"]:
            errors.append("PROTECTED_OWNER: " + path + " belongs to " + zones[0]["owner"])
        elif not any(matches(path, r) for r in task["write_set"]):
            errors.append("OUTSIDE_TASK: " + path)
    return errors


def review_errors(policy: dict, pr: dict, reviews: list[dict]) -> list[str]:
    # Only distinct human identities configured in the TRUSTED BASE can authorize.
    allowed = set(policy.get("human_reviewers", [])) - set(policy.get("agent_logins", []))
    if not allowed:
        return ["REVIEW_BLOCKED: independent human reviewers not configured"]
    latest = {}
    for review in sorted(reviews, key=lambda r: r["id"]):
        login = review["user"]["login"]
        if review["state"] != "COMMENTED":
            latest[login] = review
    author = pr["user"]["login"]
    approvals = [r for login, r in latest.items()
                 if login in allowed and login != author and r["user"]["type"] == "User"
                 and r["state"] == "APPROVED" and r["commit_id"] == pr["head"]["sha"]]
    errors = []
    if any(r["state"] == "CHANGES_REQUESTED" for r in latest.values()):
        errors.append("CHANGES_REQUESTED: unresolved review")
    if not approvals:
        errors.append("REVIEW_REQUIRED: independent approval of current head missing")
    return errors


def authorized_paths(policy: dict, pr: dict, reviews: list[dict]) -> set[str]:
    """Human approval binds exact paths to exact head; never read the PR body."""
    allowed = set(policy.get("human_reviewers", [])) - set(policy.get("agent_logins", []))
    latest = {}
    for r in sorted(reviews, key=lambda r: r["id"]):
        if r["state"] != "COMMENTED":
            latest[r["user"]["login"]] = r
    result = set()
    for login, r in latest.items():
        if (login not in allowed or login == pr["user"]["login"]
                or r["user"]["type"] != "User" or r["state"] != "APPROVED"
                or r["commit_id"] != pr["head"]["sha"]):
            continue
        for line in (r.get("body") or "").splitlines():
            if line.startswith("APEX-AUTHORIZE: "):
                result.add(line.removeprefix("APEX-AUTHORIZE: "))
    return result


def api(path: str):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "apex-coordination",
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def collection(path: str) -> list:
    result = []
    for page in range(1, 101):
        rows = api(f"{path}?per_page=100&page={page}")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise RuntimeError("pagination limit exceeded; refusing incomplete evidence")


def check(base: str, head: str, branch: str, cwd: str = ".") -> dict:
    if not SHA.fullmatch(base) or not SHA.fullmatch(head):
        raise ValueError("base/head must be full commit SHAs")
    policy = load_policy(base, cwd)
    paths = changed_paths(base, head, cwd)
    errors = violations(policy, branch, paths)
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=cwd,
                              capture_output=True).returncode
    if ancestor:
        errors.append("STALE_OR_DIVERGED: current integration is not an ancestor")
    return {"base": base, "head": head, "branch": branch, "paths": paths, "errors": errors}


def pr_check(repo: str, number: int) -> dict:
    prefix = f"repos/{repo}"
    pr = api(f"{prefix}/pulls/{number}")
    if pr["base"]["ref"] != "apex/integration" or pr["head"]["repo"]["full_name"] != repo:
        raise ValueError("wrong PR target or external head repository")
    base = api(f"{prefix}/git/ref/heads/apex/integration")["object"]["sha"]
    head = pr["head"]["sha"]
    report = check(base, head, pr["head"]["ref"])
    if pr["state"] != "open" or pr["draft"]:
        report["errors"].append("PR_NOT_READY: closed or draft")
    if pr["base"]["sha"] != base or pr["mergeable"] is not True:
        report["errors"].append("MERGEABILITY_BLOCKED: stale, conflicting or unknown")
    policy = load_policy(base)
    reviews = collection(f"{prefix}/pulls/{number}/reviews")
    authorized = authorized_paths(policy, pr, reviews)
    # Authorization never suppresses stale refs, invalid paths, unregistered branches,
    # missing review or conflicts. It only grants an exact file-level exception.
    report["errors"] = [e for e in report["errors"] if not any(
        e == f"OUTSIDE_TASK: {p}" or e == f"UNASSIGNED_OR_AMBIGUOUS: {p}"
        or e.startswith(f"PROTECTED_OWNER: {p} belongs to ") for p in authorized)]
    report["authorized_paths"] = sorted(authorized)
    report["errors"].extend(review_errors(policy, pr, reviews))
    # Detect races during API inspection; required strict checks handle later changes.
    again = api(f"{prefix}/pulls/{number}")
    now_base = api(f"{prefix}/git/ref/heads/apex/integration")["object"]["sha"]
    if again["head"]["sha"] != head or now_base != base:
        report["errors"].append("RACE: PR or integration moved; rerun")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    local = sub.add_parser("check", help="scope/freshness only, not merge authorization")
    local.add_argument("--base", required=True)
    local.add_argument("--head", required=True)
    local.add_argument("--branch", required=True)
    online = sub.add_parser("pr", help="scope, freshness, mergeability and independent review")
    online.add_argument("--repo", default="chadown62-glitch/apex-ia-")
    online.add_argument("--number", type=int, required=True)
    sync = sub.add_parser("sync", help="read-only Issues/PR checkpoint; needs GH_TOKEN")
    sync.add_argument("--repo", default="chadown62-glitch/apex-ia-")
    args = parser.parse_args()
    try:
        if args.command == "check":
            report = check(args.base, args.head, args.branch)
        elif args.command == "pr":
            report = pr_check(args.repo, args.number)
        else:
            prefix = f"repos/{args.repo}"
            report = {"integration": api(f"{prefix}/git/ref/heads/apex/integration"),
                      "issues": collection(f"{prefix}/issues"),
                      "comments": {str(n): collection(f"{prefix}/issues/{n}/comments") for n in (3, 4)},
                      "prs": collection(f"{prefix}/pulls"), "errors": []}
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 1 if report["errors"] else 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"errors": ["BLOCKED: " + type(exc).__name__ + ": " + str(exc)]}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
