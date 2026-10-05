#!/usr/bin/env python3
"""Structural metrics for Python source, with no dependencies.

One implementation, two callers. The PreToolUse hook imports this to decide the
complexity ratchet, and CI runs it as a script against a pull request's base.
A second copy would drift, and the gate would then pass locally and fail in CI
for reasons nobody could reproduce.

STDLIB ONLY. The hook cannot import ``mgcp`` or anything from site-packages, so
neither can this. ``ast`` gives every metric here.

THE RATCHET, NOT AN ABSOLUTE LIMIT. 58 of this repository's 463 functions are
already over cyclomatic complexity 10, and ``init_project.main`` is at 59. A
fixed limit would refuse every commit that touches them. So changed code may
not get worse on a metric it already fails, and new code must meet the limits.

Cyclomatic complexity here is ``1 + one per branch point``, counting ``if``,
``for``, ``while``, ``except``, each comprehension, ``assert``, each
conditional expression, each ``match`` case, and one per boolean OPERATOR
(``len(BoolOp.values) - 1``, so ``a and b`` scores 1 and ``a and b and c``
scores 2). That last rule is the one that matters: counting one per boolean
OPERAND instead puts 66 functions over the limit rather than 58, and the limits
were calibrated against 58. ``tests/test_quality_metrics.py`` pins the
whole-repository counts so the definition cannot drift unnoticed.

Run it directly:

    python quality_metrics.py --report src/            # current state
    python quality_metrics.py --base origin/main       # ratchet vs a git ref
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import subprocess
import sys

DEFAULT_LIMITS = {
    "cyclomatic": 10,
    "length": 80,
    "depth": 4,
    "params": 6,
    "file_lines": 1000,
}

# Each metric's human name, for deny reasons.
METRIC_LABELS = {
    "cyclomatic": "CC",
    "length": "lines",
    "depth": "nesting",
    "params": "params",
    "file_lines": "file lines",
}

_BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.comprehension,
    ast.Assert,
    ast.IfExp,
)

_NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                  ast.AsyncWith)

_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

ALLOW_BROAD_EXCEPT = "mgcp: allow-broad-except"


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _cyclomatic(node: ast.AST) -> int:
    """1 + one per branch point. See the module docstring for the exact set."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCH_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            # One per OPERATOR, not per operand.
            score += len(child.values) - 1
        elif hasattr(ast, "match_case") and isinstance(child, ast.match_case):
            score += 1
    return score


def _depth(node: ast.AST) -> int:
    """Deepest block nesting inside one function.

    A nested ``def`` resets rather than accumulates, because its body is that
    function's nesting and not this one's.
    """

    def walk(n: ast.AST, current: int) -> int:
        deepest = current
        for child in ast.iter_child_nodes(n):
            if isinstance(child, _FUNC_NODES + (ast.ClassDef,)):
                continue
            step = 1 if isinstance(child, _NESTING_NODES) else 0
            deepest = max(deepest, walk(child, current + step))
        return deepest

    return walk(node, 0)


def _params(node: ast.AST) -> int:
    a = node.args
    return len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)


def _is_mcp_tool(node: ast.AST) -> bool:
    """True for a function registered as an MCP tool.

    Its parameter list is its wire protocol. A tool taking eight arguments is
    eight fields in a schema the model fills in, not a function that needs
    splitting, so the parameter metric does not apply to it.
    """
    for dec in getattr(node, "decorator_list", []):
        if "mcp.tool" in ast.unparse(dec):
            return True
    return False


def measure_source(source: str, path: str = "<string>") -> dict:
    """Metrics for one file. Returns {} when the source does not parse.

    An unparseable file is skipped rather than reported, because the ratchet
    compares two versions and a syntax error on either side makes the
    comparison meaningless. The caller allows in that case.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {}

    functions: dict[str, dict] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, _FUNC_NODES):
                name = f"{prefix}{child.name}"
                # A duplicate qualified name means a conditional definition or
                # a nested redefinition. Keep the first; comparing against the
                # wrong twin is worse than skipping the second.
                if name not in functions:
                    functions[name] = {
                        "cyclomatic": _cyclomatic(child),
                        "length": (child.end_lineno or child.lineno) - child.lineno + 1,
                        "depth": _depth(child),
                        "params": _params(child),
                        "lineno": child.lineno,
                        "is_mcp_tool": _is_mcp_tool(child),
                    }
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return {
        "path": path,
        "file_lines": len(source.splitlines()),
        "functions": functions,
    }


# ---------------------------------------------------------------------------
# Banned patterns
# ---------------------------------------------------------------------------


def _swallowed_handlers(tree: ast.AST, source_lines: list[str]) -> list[dict]:
    """Broad excepts whose body discards the error.

    Allowed with ``# mgcp: allow-broad-except <reason>`` on the handler line.
    The hook's own fail-open handlers need that escape, and a rule that refuses
    the code implementing it would be self-defeating.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
        )
        if not broad:
            continue
        body = node.body
        if len(body) != 1:
            continue
        stmt = body[0]
        discards = (
            isinstance(stmt, ast.Pass)
            or isinstance(stmt, ast.Continue)
            or (isinstance(stmt, ast.Return) and (
                stmt.value is None or isinstance(stmt.value, ast.Constant)))
        )
        if not discards:
            continue
        line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
        if ALLOW_BROAD_EXCEPT in line:
            continue
        out.append({"pattern": "swallowed_error", "lineno": node.lineno,
                    "detail": "broad except whose body discards the error"})
    return out


def _pass_through_wrappers(tree: ast.AST) -> list[dict]:
    """Functions whose body is one return of a call forwarding their own args.

    A layer that adds nothing is a layer to read, and this is the shape it
    takes. A wrapper that reorders, defaults or renames anything is not
    reported, because it is doing work.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, _FUNC_NODES):
            continue
        body = [s for s in node.body if not isinstance(s, ast.Expr)
                or not isinstance(s.value, ast.Constant)]  # drop the docstring
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue
        call = body[0].value
        if not isinstance(call, ast.Call):
            continue
        own = [a.arg for a in node.args.posonlyargs + node.args.args]
        if not own:
            continue
        passed = [a.id for a in call.args if isinstance(a, ast.Name)]
        if passed == own and not call.keywords:
            out.append({"pattern": "pass_through_wrapper", "lineno": node.lineno,
                        "detail": f"{node.name} only forwards its parameters"})
    return out


def banned_patterns(source: str, checks: list[str]) -> list[dict]:
    """Banned patterns in one file. Empty when the source does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []
    lines = source.splitlines()
    out = []
    if "swallowed_error" in checks:
        out += _swallowed_handlers(tree, lines)
    if "pass_through_wrapper" in checks:
        out += _pass_through_wrappers(tree)
    return out


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def compare(before: dict, after: dict, limits: dict | None = None,
            skip_file_length: bool = False) -> list[dict]:
    """Violations introduced between two versions of one file.

    ``before`` is {} when the file is new, which holds every function in it to
    the full limits. A rename reads as a delete plus an add for the same
    reason, and that is deliberate: renaming a long function is the cheapest
    moment to split it.
    """
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    if not after:
        return []

    out = []
    old_funcs = (before or {}).get("functions", {})
    for name, now in after.get("functions", {}).items():
        was = old_funcs.get(name)
        for metric, limit in limits.items():
            if metric == "file_lines":
                continue
            if metric == "params" and now.get("is_mcp_tool"):
                continue
            value = now[metric]
            if was is None:
                if value > limit:
                    out.append({
                        "name": name, "metric": metric, "before": None,
                        "after": value, "limit": limit, "lineno": now["lineno"],
                        "reason": "new function starts over the limit",
                    })
            else:
                prior = was[metric]
                worse = value > prior
                over = value > limit
                # Already over and getting worse, or newly crossing.
                if over and (worse or prior <= limit):
                    if worse or prior <= limit < value:
                        out.append({
                            "name": name, "metric": metric, "before": prior,
                            "after": value, "limit": limit, "lineno": now["lineno"],
                            "reason": "crossed the limit" if prior <= limit
                                      else "already over the limit and got worse",
                        })

    # File length ratchets on net growth once over the limit.
    file_limit = None if skip_file_length else limits.get("file_lines")
    if file_limit:
        now_lines = after.get("file_lines", 0)
        was_lines = (before or {}).get("file_lines")
        if now_lines > file_limit and (was_lines is None or now_lines > was_lines):
            out.append({
                "name": after.get("path", "<file>"), "metric": "file_lines",
                "before": was_lines, "after": now_lines, "limit": file_limit,
                "lineno": 1,
                "reason": "new file over the limit" if was_lines is None
                          else "already over the limit and grew",
            })
    return out


def format_violations(violations: list[dict]) -> str:
    """One line per violation, with before and after."""
    lines = []
    for v in violations:
        label = METRIC_LABELS.get(v["metric"], v["metric"])
        if v["before"] is None:
            lines.append(f"  {v['name']}: {label} {v['after']} (limit {v['limit']}), "
                         f"{v['reason']}")
        else:
            lines.append(f"  {v['name']}: {label} {v['before']} -> {v['after']} "
                         f"(limit {v['limit']}), {v['reason']}")
    return "\n".join(lines)


def excluded(path: str, globs: list[str]) -> bool:
    """True when a repo-relative POSIX path matches any glob.

    The same semantics as ``_path_excluded`` in the dispatcher, and the two must
    agree. They are separate because the hook cannot assume this module is
    installed, so its own copy has to work without it. A test pins them to the
    same answers.
    """
    for g in globs or []:
        try:
            if fnmatch.fnmatch(path, g):
                return True
            # fnmatch cannot express "zero or more directories".
            if "/**/" in g and fnmatch.fnmatch(path, g.replace("/**/", "/", 1)):
                return True
            if g.endswith("/**") and fnmatch.fnmatch(path, g[:-3] + "/*"):
                return True
        except Exception:  # mgcp: allow-broad-except a bad glob must not block
            continue
    return False


# ---------------------------------------------------------------------------
# Script entry: CI and reporting
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: str = ".") -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                           text=True, timeout=30)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _report(paths: list[str], limits: dict) -> int:
    """Print the current distribution. Always exits 0; this is information."""
    import statistics
    from pathlib import Path

    rows = []
    for root in paths:
        for p in sorted(Path(root).rglob("*.py")):
            m = measure_source(p.read_text(errors="replace"), str(p))
            for name, f in m.get("functions", {}).items():
                rows.append((f, f"{p}:{name}"))
    if not rows:
        print("no Python files found")
        return 0

    print(f"{len(rows)} functions\n")
    print(f"  {'metric':12s} {'median':>7} {'p90':>7} {'max':>7} {'over limit':>11}")
    for metric in ("cyclomatic", "length", "depth", "params"):
        vals = sorted(f[metric] for f, _ in rows)
        p90 = vals[int(len(vals) * 0.9)] if vals else 0
        over = sum(1 for v in vals if v > limits[metric])
        print(f"  {metric:12s} {statistics.median(vals):>7.0f} {p90:>7} "
              f"{max(vals):>7} {over:>11}")
    print("\n  worst by cyclomatic complexity:")
    for f, name in sorted(rows, key=lambda r: -r[0]["cyclomatic"])[:5]:
        print(f"    CC {f['cyclomatic']:3d}  {f['length']:4d} lines  {name}")
    return 0


def _ratchet_against_base(base: str, cwd: str, limits: dict,
                          exclude: list[str], checks: list[str],
                          length_exempt: list[str] | None = None) -> int:
    """Compare the working tree against a git ref. Non-zero on a violation."""
    rc, _ = _git(["rev-parse", "--verify", "-q", f"{base}^{{commit}}"], cwd)
    if rc != 0:
        print(f"cannot resolve base ref {base!r}. A gate that cannot find its "
              f"base reports nothing and must not report success.", file=sys.stderr)
        return 2

    rc, out = _git(["diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"], cwd)
    if rc != 0:
        print(f"git diff against {base!r} failed", file=sys.stderr)
        return 2
    changed = [p for p in out.splitlines() if p.strip()]
    if not changed:
        print(f"no Python changed against {base}")
        return 0

    total = []
    for path in changed:
        if excluded(path, exclude):
            continue
        rc_b, before_src = _git(["show", f"{base}:{path}"], cwd)
        try:
            with open(f"{cwd}/{path}", errors="replace") as fh:
                after_src = fh.read()
        except OSError:
            continue
        before = measure_source(before_src, path) if rc_b == 0 else {}
        after = measure_source(after_src, path)
        viol = compare(before, after, limits,
                       excluded(path, length_exempt or []))
        for v in viol:
            v["path"] = path
        total += viol
        for b in banned_patterns(after_src, checks):
            b["path"] = path
            b["name"] = f"{path}:{b['lineno']}"
            b["metric"] = b["pattern"]
            b["before"] = None
            b["after"] = b["detail"]
            b["limit"] = "banned"
            total.append(b)

    if not total:
        print(f"{len(changed)} changed Python file(s), no violations against {base}")
        return 0
    print(f"{len(total)} violation(s) against {base}:\n")
    print(format_violations(total))
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", help="git ref to ratchet the working tree against")
    ap.add_argument("--report", nargs="*", metavar="PATH",
                    help="print the current distribution for these paths")
    ap.add_argument("--cwd", default=".", help="repository root")
    ap.add_argument("--exclude", nargs="*", default=["tests/**", "docs/**"])
    ap.add_argument(
        "--file-length-exempt", nargs="*", default=[], metavar="GLOB",
        help=("skip the FILE LENGTH row for these paths while still measuring "
              "their functions. The hook rule has the same field, and the two "
              "callers must agree or a commit passes in one and fails in the "
              "other."),
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.report is not None:
        return _report(args.report or ["src"], DEFAULT_LIMITS)
    if args.base:
        if args.json:
            print(json.dumps({"base": args.base}))
        return _ratchet_against_base(args.base, args.cwd, DEFAULT_LIMITS,
                                     args.exclude, list(METRIC_LABELS),
                                     args.file_length_exempt)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
