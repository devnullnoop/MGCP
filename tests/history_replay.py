#!/usr/bin/env python3
"""Replay the planned commit gates over this repository's own history.

The Validation section of ``update_plan.md`` asks one question before any gate
is allowed to refuse a tool call: how many of the last 200 commits would each
gate have refused, and which five are the worst per gate. Limits picked from a
distribution table are a guess. Limits checked against real commits are not.

Read the per-gate offender lists by hand. If most refusals are commits you
would defend, the limit is wrong and not the commit. That judgement is the
output of this script; the counts are only how you get to it.

READ ONLY. Every git call goes through ``_git`` as ``git -C <repo> show`` or
``git -C <repo> diff``. Nothing is checked out, no worktree is created and the
repository is never written. A replay that moved HEAD to measure history would
be a worse tool than no replay.

The metric code comes from ``src/mgcp/hook_templates/quality_metrics.py``, the
same module the hook imports and CI runs. A second copy of the measurement here
would drift, and the replay would then calibrate limits for a gate that scores
differently.

Run it:

    python tests/history_replay.py                  # last 200 commits
    python tests/history_replay.py --commits 50
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exclude globs the seeded rules carry in update_plan.md. Test and
# documentation churn is never what the gates are for, so counting it would
# refuse the commits that fix the problem.
EXCLUDE_GLOBS = ["tests/**", "docs/**", "*.md", "**/bootstrap_data/**"]

# server.py is 3,399 lines and holds all 50 MCP tools. The plan's open decision
# is to split it or exempt it; until it is split, the complexity rule alone
# skips it, because otherwise every new tool is refused for a reason that has
# nothing to do with the tool. The diff and message rules still cover it.
COMPLEXITY_EXTRA_EXCLUDE = ["src/mgcp/server.py"]

BANNED_CHECKS = ["swallowed_error", "pass_through_wrapper"]

DIFF_BUDGET_ADDED = 300
WHY_MIN_ADDED = 40
WHY_PATTERN = r"(?ms)^Why:\s+\S.{39,}"

GATES = ("complexity", "diff_budget", "why")
GATE_LABELS = {
    "complexity": "commit-complexity-ratchet",
    "diff_budget": f"edit-diff-budget (>{DIFF_BUDGET_ADDED} added)",
    "why": f"commit-requires-why (>{WHY_MIN_ADDED} added)",
}


def _load_quality_metrics() -> ModuleType:
    """Import the hook's metric module by path.

    It sits outside any package, because the hook runs as a loose script with
    no ``mgcp`` on its path. A plain import would need a sys.path edit at module
    scope, which ruff rejects, so the load happens here instead.
    """
    path = REPO_ROOT / "src" / "mgcp" / "hook_templates" / "quality_metrics.py"
    spec = importlib.util.spec_from_file_location("quality_metrics", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load metric module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qm = _load_quality_metrics()


def _git(repo: str, args: list[str]) -> tuple[int, str]:
    """One read-only git call. Returns the exit code and stdout.

    The exit code is handed back rather than raised, because a missing blob is
    the normal signal for "this file is new in this commit".
    """
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, timeout=60
    )
    return result.returncode, result.stdout


def _commits(repo: str, limit: int) -> list[tuple[str, str]]:
    """The newest ``limit`` commits as (sha, subject), newest first.

    ``--first-parent`` is what makes "compare against its first parent" true for
    every row. Without it a merge's second parent appears as its own commit, and
    its changes are then counted twice.
    """
    rc, out = _git(repo, ["log", "--first-parent", f"-n{limit}",
                          "--format=%H%x1f%s"])
    if rc != 0:
        raise SystemExit(f"cannot read the commit log of {repo}")
    rows = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subject = line.split("\x1f", 1)
            rows.append((sha, subject))
    return rows


def _first_parent(repo: str, sha: str) -> str | None:
    rc, out = _git(repo, ["rev-parse", "--verify", "-q", f"{sha}^1"])
    if rc != 0:
        return None
    return out.strip() or None


def _added_lines(repo: str, parent: str, sha: str) -> tuple[int, list[str]]:
    """Added lines and touched paths between two commits, excludes applied.

    Only additions count. The plan's diff budget exists to keep a change
    reviewable, and a commit that deletes 200 lines is not the problem it is
    trying to catch.
    """
    rc, out = _git(repo, ["diff", "--numstat", parent, sha])
    if rc != 0:
        return 0, []
    added = 0
    paths = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        plus, _minus, path = parts
        if plus == "-":
            continue  # binary file; numstat reports no line counts
        if qm.excluded(path, EXCLUDE_GLOBS):
            continue
        added += int(plus)
        paths.append(path)
    return added, paths


def _changed_python(repo: str, parent: str, sha: str) -> list[str]:
    rc, out = _git(repo, ["diff", "--name-only", "--diff-filter=ACMR",
                          parent, sha, "--", "*.py"])
    if rc != 0:
        return []
    exclude = EXCLUDE_GLOBS + COMPLEXITY_EXTRA_EXCLUDE
    return [p for p in out.splitlines()
            if p.strip() and not qm.excluded(p, exclude)]


class _BlobCache:
    """Measured metrics per (rev, path).

    Walking back from HEAD reads each revision twice: once as a commit's own
    version and once as its child's baseline. The cache halves the ``git show``
    calls, which is the whole cost of the replay.
    """

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self._source: dict[tuple[str, str], str | None] = {}
        self._measured: dict[tuple[str, str], dict] = {}

    def source(self, rev: str, path: str) -> str | None:
        key = (rev, path)
        if key not in self._source:
            rc, out = _git(self.repo, ["show", f"{rev}:{path}"])
            self._source[key] = out if rc == 0 else None
        return self._source[key]

    def measured(self, rev: str, path: str) -> dict:
        key = (rev, path)
        if key not in self._measured:
            src = self.source(rev, path)
            self._measured[key] = qm.measure_source(src, path) if src else {}
        return self._measured[key]


def _new_banned_patterns(cache: _BlobCache, parent: str, sha: str,
                         path: str) -> list[dict]:
    """Banned patterns the commit adds, not ones it inherits.

    The plan's wording is "New ``except Exception:``". Reporting every broad
    handler already in the file would refuse a one-line edit to a file somebody
    else wrote, which is the absolute limit the ratchet exists to avoid.
    Matching is on (pattern, detail) rather than line number, so moving code
    down a file is not a new finding.
    """
    after_src = cache.source(sha, path)
    if not after_src:
        return []
    after = qm.banned_patterns(after_src, BANNED_CHECKS)
    if not after:
        return []
    before_src = cache.source(parent, path)
    before = qm.banned_patterns(before_src, BANNED_CHECKS) if before_src else []
    seen = {(b["pattern"], b["detail"]) for b in before}
    return [a for a in after if (a["pattern"], a["detail"]) not in seen]


def _complexity_violations(cache: _BlobCache, parent: str, sha: str,
                           paths: list[str]) -> list[dict]:
    out = []
    for path in paths:
        before = cache.measured(parent, path)
        after = cache.measured(sha, path)
        if not after:
            continue  # unparseable or deleted; the gate allows in that case
        for violation in qm.compare(before, after):
            violation["path"] = path
            if violation["metric"] != "file_lines":
                # compare() keys functions by qualified name with no path, and
                # four of this repository's hook scripts each define main().
                # Without the path the offender list printed the same line four
                # times and a reader could not tell which file to open.
                violation["name"] = f"{path}:{violation['name']}"
            out.append(violation)
        for banned in _new_banned_patterns(cache, parent, sha, path):
            out.append({
                "path": path,
                "name": f"{path}:{banned['lineno']}",
                "metric": banned["pattern"],
                "before": None,
                "after": banned["detail"],
                "limit": "banned",
                "reason": "banned pattern added",
            })
    return out


def replay(repo: str, limit: int) -> list[dict]:
    """One row per commit, with each gate's verdict."""
    cache = _BlobCache(repo)
    why_re = re.compile(WHY_PATTERN)
    rows = []
    for sha, subject in _commits(repo, limit):
        parent = _first_parent(repo, sha)
        if parent is None:
            continue  # the root commit has no baseline to ratchet against
        added, _paths = _added_lines(repo, parent, sha)
        rc, message = _git(repo, ["show", "-s", "--format=%B", sha])
        has_why = bool(why_re.search(message)) if rc == 0 else False
        violations = _complexity_violations(
            cache, parent, sha, _changed_python(repo, parent, sha))
        rows.append({
            "sha": sha,
            "subject": subject,
            "added": added,
            "has_why": has_why,
            "violations": violations,
            "refused": {
                "complexity": bool(violations),
                "diff_budget": added > DIFF_BUDGET_ADDED,
                "why": added > WHY_MIN_ADDED and not has_why,
            },
            # A gate that does not apply to a commit is not evidence either way,
            # so the rate is reported over the commits it could have refused.
            "eligible": {
                "complexity": True,
                "diff_budget": True,
                "why": added > WHY_MIN_ADDED,
            },
        })
    return rows


def _rank(rows: list[dict], gate: str) -> list[dict]:
    """The worst refusals first.

    The complexity gate ranks by violation count, the other two by added lines,
    because that is the number a reader has to judge against the limit.
    """
    refused = [r for r in rows if r["refused"][gate]]
    if gate == "complexity":
        return sorted(refused, key=lambda r: (-len(r["violations"]), -r["added"]))
    return sorted(refused, key=lambda r: -r["added"])


def _print_counts(rows: list[dict]) -> None:
    print(f"{len(rows)} commits replayed, each against its first parent\n")
    print(f"  {'gate':34s} {'eligible':>9} {'refused':>8} {'rate':>7}")
    for gate in GATES:
        eligible = sum(1 for r in rows if r["eligible"][gate])
        refused = sum(1 for r in rows if r["refused"][gate])
        rate = (refused / eligible * 100) if eligible else 0.0
        print(f"  {GATE_LABELS[gate]:34s} {eligible:>9} {refused:>8} {rate:>6.1f}%")

    with_why = sum(1 for r in rows if r["has_why"])
    print(f"\n  Why: paragraph present in {with_why} of {len(rows)} commit messages")
    if rows:
        added = sorted(r["added"] for r in rows)
        median = added[len(added) // 2]
        print(f"  added lines per commit: median {median}, max {max(added)} "
              f"(budget {DIFF_BUDGET_ADDED})")


def _print_offenders(rows: list[dict], worst: int) -> None:
    for gate in GATES:
        ranked = _rank(rows, gate)
        print(f"\n{GATE_LABELS[gate]}: {len(ranked)} refused, "
              f"worst {min(worst, len(ranked))}")
        if not ranked:
            print("  none")
            continue
        for row in ranked[:worst]:
            detail = (f"{len(row['violations'])} violation(s)"
                      if gate == "complexity" else f"{row['added']} added")
            print(f"  {row['sha'][:8]}  {detail:<18} {row['subject'][:72]}")
            if gate == "complexity":
                print(qm.format_violations(row["violations"][:6]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--commits", type=int, default=200,
                        help="how many commits to walk back from HEAD")
    parser.add_argument("--repo", default=str(REPO_ROOT),
                        help="repository to read; never written")
    parser.add_argument("--worst", type=int, default=5,
                        help="offenders listed per gate")
    args = parser.parse_args(argv)

    rows = replay(args.repo, args.commits)
    if not rows:
        print("no commits with a first parent were found", file=sys.stderr)
        return 1
    _print_counts(rows)
    _print_offenders(rows, args.worst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
