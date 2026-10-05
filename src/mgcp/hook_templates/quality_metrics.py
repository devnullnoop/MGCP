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

# Every banned pattern this module can look for. Named once, because a caller
# passing a misspelled check silently looks for nothing.
BANNED_CHECKS = ("swallowed_error", "pass_through_wrapper")

# Declares one function deliberately large, on its def line, with a reason:
#
#     def main():  # mgcp: allow-size a build script is one linear sequence
#
# Some work is honestly one long function. A build script, an installer, an
# argparse dispatcher: each branch is a flag, the sequence is flat, and splitting
# it into eight helpers called once would scatter a procedure that reads top to
# bottom. Those functions still measure large, and the ratchet would refuse the
# next edit that adds a branch, which is a refusal with nothing wrong behind it.
#
# The escape needs a reason, because an exemption nobody justified is a hole. A
# bare marker does not exempt anything. Exempt functions are COUNTED and NAMED in
# the report rather than hidden, so the list stays something a person decided
# rather than something that drifted.
ALLOW_SIZE = "mgcp: allow-size"


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
    source_lines = source.splitlines()

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
                        "size_exempt": _size_exemption(child, source_lines),
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
        if not (_catches_broadly(node) and _body_discards(node)):
            continue
        if _allowed_on_its_line(node, source_lines):
            continue
        out.append({"pattern": "swallowed_error", "lineno": node.lineno,
                    "detail": "broad except whose body discards the error"})
    return out


def _catches_broadly(handler: ast.ExceptHandler) -> bool:
    """True for a bare except, or one naming Exception or BaseException."""
    if handler.type is None:
        return True
    return (isinstance(handler.type, ast.Name)
            and handler.type.id in ("Exception", "BaseException"))


def _body_discards(handler: ast.ExceptHandler) -> bool:
    """True when the handler's whole body throws the error away.

    One statement, and that statement is pass, continue, or a return of nothing
    or a constant. Logging the error, re-raising, or returning something built
    from it all read as handling it.
    """
    if len(handler.body) != 1:
        return False
    stmt = handler.body[0]
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    return (isinstance(stmt, ast.Return)
            and (stmt.value is None or isinstance(stmt.value, ast.Constant)))


def _allowed_on_its_line(handler: ast.ExceptHandler, source_lines: list[str]) -> bool:
    """True when the handler line carries the escape comment."""
    if handler.lineno > len(source_lines):
        return False
    return ALLOW_BROAD_EXCEPT in source_lines[handler.lineno - 1]


def _pass_through_wrappers(tree: ast.AST) -> list[dict]:
    """Functions whose body is one return of a call forwarding their own args.

    A layer that adds nothing is a layer to read, and this is the shape it
    takes. A wrapper that reorders, defaults or renames anything is not
    reported, because it is doing work.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_NODES) and _only_forwards(node):
            out.append({"pattern": "pass_through_wrapper", "lineno": node.lineno,
                        "detail": f"{node.name} only forwards its parameters"})
    return out


def _sole_returned_call(node) -> ast.Call | None:
    """The call this function returns, if its body is only that return.

    The docstring is dropped first, so a documented one-line wrapper still
    reads as one statement.
    """
    body = [s for s in node.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    call = body[0].value
    return call if isinstance(call, ast.Call) else None


def _only_forwards(node) -> bool:
    """True when the function returns a call passing its own parameters in order.

    Any reorder, rename, default or keyword means the wrapper is doing work, so
    it is not reported.
    """
    call = _sole_returned_call(node)
    if call is None or call.keywords:
        return False
    own = [a.arg for a in node.args.posonlyargs + node.args.args]
    if not own:
        return False
    passed = [a.id for a in call.args if isinstance(a, ast.Name)]
    return passed == own


def _size_exemption(node, source_lines: list[str]) -> str:
    """The stated reason this function is deliberately large, else "".

    Read from the ``def`` line, from any decorator line, and from the comment
    line directly above, so the marker can sit wherever it fits. The line above
    is not a convenience: a signature long enough to need the exemption is
    often already at the line length limit, so the def line has no room for it.

    A marker with no reason after it returns "", which exempts nothing. An
    exemption nobody justified is a hole.
    """
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    for lineno in range(max(1, first - 1), (node.lineno or first) + 1):
        if lineno > len(source_lines):
            break
        line = source_lines[lineno - 1]
        if ALLOW_SIZE not in line:
            continue
        reason = line.split(ALLOW_SIZE, 1)[1].strip(" #:\t")
        if reason:
            return reason
    return ""


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
            if _metric_applies(metric, now):
                found = _metric_violation(name, metric, limit, now, was)
                if found:
                    out.append(found)

    found = _file_length_violation(before, after, limits, skip_file_length)
    if found:
        out.append(found)
    return out


def _metric_applies(metric: str, now: dict) -> bool:
    """Whether one metric is measured for one function.

    File length is a property of the file and handled once. A function
    registered as an MCP tool is exempt from the parameter count, because its
    parameters are a wire schema the model fills in rather than a signature that
    wants splitting. A function carrying the allow-size marker with a reason is
    exempt from all four, because some work is honestly one long sequence and
    the ratchet would otherwise refuse the next branch added to it.
    """
    if metric == "file_lines":
        return False
    if now.get("size_exempt"):
        return False
    return not (metric == "params" and now.get("is_mcp_tool"))


def _metric_violation(name, metric, limit, now, was):
    """One violation for one function and metric, or None.

    A new function is held to the full limit. An existing one is refused when it
    crosses the limit, or when it was already over and got worse. An existing
    function that improves while still over the limit is allowed, which is the
    whole point of a ratchet.
    """
    value = now[metric]
    if was is None:
        if value <= limit:
            return None
        return {"name": name, "metric": metric, "before": None, "after": value,
                "limit": limit, "lineno": now["lineno"],
                "reason": "new function starts over the limit"}

    prior = was[metric]
    if value <= limit or value <= prior:
        return None
    return {
        "name": name, "metric": metric, "before": prior, "after": value,
        "limit": limit, "lineno": now["lineno"],
        "reason": ("crossed the limit" if prior <= limit
                   else "already over the limit and got worse"),
    }


def _file_length_violation(before, after, limits, skip):
    """The file-length violation for this change, or None.

    Once a file is over the limit it may not grow NET. Shrinking it while still
    over the limit is allowed.
    """
    limit = None if skip else limits.get("file_lines")
    if not limit:
        return None
    now_lines = after.get("file_lines", 0)
    was_lines = (before or {}).get("file_lines")
    if now_lines <= limit:
        return None
    if was_lines is not None and now_lines <= was_lines:
        return None
    return {
        "name": after.get("path", "<file>"), "metric": "file_lines",
        "before": was_lines, "after": now_lines, "limit": limit, "lineno": 1,
        "reason": ("new file over the limit" if was_lines is None
                   else "already over the limit and grew"),
    }


def format_violations(violations: list[dict]) -> str:
    """One line per violation, naming the file, the line and the change.

    The file name is not optional. This text is the deny reason the hook shows,
    and a report reading "f: CC 17 (limit 10)" does not say which file holds f,
    so a commit touching several files gave no way to find the offender.
    """
    return "\n".join(f"  {_violation_label(v)}: {_violation_change(v)}"
                      for v in violations)


def _violation_label(v: dict) -> str:
    """Where the violation is: path, line and name, as much as is known."""
    where = v.get("path") or ""
    lineno = v.get("lineno")
    if where and lineno:
        where = f"{where}:{lineno}"
    name = v.get("name") or ""
    if where and name and not name.startswith(where):
        return f"{where} {name}"
    return where or name or "<unknown>"


def _violation_change(v: dict) -> str:
    """What the metric did, and why that is refused."""
    label = METRIC_LABELS.get(v["metric"], v["metric"])
    if v["before"] is None:
        return f"{label} {v['after']} (limit {v['limit']}), {v['reason']}"
    return (f"{label} {v['before']} -> {v['after']} "
            f"(limit {v['limit']}), {v['reason']}")


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


def _measure_tree(paths: list[str]) -> list:
    """Every function under these paths, as (metrics, label) pairs."""
    from pathlib import Path as _Path

    rows = []
    for root in paths:
        for p in sorted(_Path(root).rglob("*.py")):
            m = measure_source(p.read_text(errors="replace"), str(p))
            for name, f in m.get("functions", {}).items():
                rows.append((f, f"{p}:{name}"))
    return rows


def _print_distribution(rows: list, limits: dict) -> None:
    """Median, p90, max and the over-limit count for each metric."""
    import statistics

    print(f"  {'metric':12s} {'median':>7} {'p90':>7} {'max':>7} {'over limit':>11}")
    for metric in ("cyclomatic", "length", "depth", "params"):
        vals = sorted(f[metric] for f, _ in rows)
        p90 = vals[int(len(vals) * 0.9)] if vals else 0
        over = sum(1 for v in vals if v > limits[metric])
        print(f"  {metric:12s} {statistics.median(vals):>7.0f} {p90:>7} "
              f"{max(vals):>7} {over:>11}")


def _print_exemptions(rows: list) -> None:
    """The functions declared deliberately large, with the stated reason.

    Named rather than hidden. An exemption list nobody can see is one that
    drifts, and the point of requiring a reason is that someone reads it.
    """
    exempt = [(f, n) for f, n in rows if f.get("size_exempt")]
    if not exempt:
        return
    print(f"\n  declared deliberately large ({len(exempt)}), with the reason given:")
    for f, name in sorted(exempt, key=lambda r: -r[0]["cyclomatic"]):
        print(f"    CC {f['cyclomatic']:3d}  {f['length']:4d} lines  {name}")
        print(f"             {f['size_exempt']}")


def _report(paths: list[str], limits: dict) -> int:
    """Print the current distribution. Always exits 0; this is information."""
    rows = _measure_tree(paths)
    if not rows:
        print("no Python files found")
        return 0

    print(f"{len(rows)} functions\n")
    _print_distribution(rows, limits)

    # "Over limit" is not "refused". The ratchet holds an existing function at
    # its current size and only refuses it getting worse, so a large legacy
    # function blocks nothing. Saying so here stops the count reading as a
    # backlog of failures.
    print("\n  Over limit is not refused. The ratchet holds these at their")
    print("  current size and refuses only a change that makes one worse.")

    print("\n  worst by cyclomatic complexity:")
    for f, name in sorted(rows, key=lambda r: -r[0]["cyclomatic"])[:5]:
        mark = "  exempt" if f.get("size_exempt") else ""
        print(f"    CC {f['cyclomatic']:3d}  {f['length']:4d} lines  {name}{mark}")

    _print_exemptions(rows)
    return 0


def _changed_python(base: str, cwd: str) -> tuple[list[str], int]:
    """Python paths differing from the base ref, and an exit code.

    An exit code of 2 means the question could not be answered, which is not the
    same as an answer of none. Untracked files are included and read as new,
    because git diff cannot see them and a new file is the one case where the
    limits apply in full. Leaving them out reported "no Python changed" for code
    this had never looked at.
    """
    rc, out = _git(["diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.py"], cwd)
    if rc != 0:
        print(f"git diff against {base!r} failed", file=sys.stderr)
        return [], 2
    changed = [p for p in out.splitlines() if p.strip()]
    rc_u, out_u = _git(["ls-files", "--others", "--exclude-standard", "--", "*.py"], cwd)
    if rc_u == 0:
        changed += [p for p in out_u.splitlines() if p.strip() and p not in changed]
    return changed, 0


def _violations_for_path(path, base, cwd, limits, checks, length_exempt):
    """Every violation one file contributes, metric and banned pattern alike.

    A file that cannot be read contributes nothing rather than failing the run,
    because a path git lists and the filesystem does not have is a race with a
    concurrent checkout, not a code defect.
    """
    rc_b, before_src = _git(["show", f"{base}:{path}"], cwd)
    try:
        with open(f"{cwd}/{path}", errors="replace") as fh:
            after_src = fh.read()
    except OSError:
        return []

    before = measure_source(before_src, path) if rc_b == 0 else {}
    found = compare(before, measure_source(after_src, path), limits,
                    excluded(path, length_exempt or []))
    for v in found:
        v["path"] = path
    for b in banned_patterns(after_src, checks):
        b.update(path=path, name="", metric=b["pattern"], before=None,
                 after=b["detail"], limit="banned", reason="banned pattern")
        found.append(b)
    return found


def _ratchet_against_base(base: str, cwd: str, limits: dict,
                          exclude: list[str], checks: list[str],
                          length_exempt: list[str] | None = None) -> int:
    """Compare the working tree against a git ref. Non-zero on a violation."""
    rc, _ = _git(["rev-parse", "--verify", "-q", f"{base}^{{commit}}"], cwd)
    if rc != 0:
        print(f"cannot resolve base ref {base!r}. A gate that cannot find its "
              f"base reports nothing and must not report success.", file=sys.stderr)
        return 2

    changed, rc = _changed_python(base, cwd)
    if rc != 0:
        return rc
    if not changed:
        print(f"no Python changed against {base}")
        return 0

    total = []
    for path in changed:
        if not excluded(path, exclude):
            total += _violations_for_path(path, base, cwd, limits, checks,
                                          length_exempt)

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
    ap.add_argument(
        "--checks", nargs="*", default=list(BANNED_CHECKS), metavar="NAME",
        choices=list(BANNED_CHECKS),
        help=("banned patterns to look for. Defaults to all of them, so this "
              "matches the hook rule, whose banned field names the same two."),
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.report is not None:
        return _report(args.report or ["src"], DEFAULT_LIMITS)
    if args.base:
        if args.json:
            print(json.dumps({"base": args.base}))
        return _ratchet_against_base(args.base, args.cwd, DEFAULT_LIMITS,
                                     args.exclude, args.checks,
                                     args.file_length_exempt)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
