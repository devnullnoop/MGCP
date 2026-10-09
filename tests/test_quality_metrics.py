"""Tests for src/mgcp/hook_templates/quality_metrics.py, the Gate 2 metric module.

The module is stdlib-only because the PreToolUse hook imports it, and that hook
may not import ``mgcp`` or anything from site-packages. These tests load it the
way the hook does, by putting its directory on ``sys.path``. Its sibling hooks
have hyphens in their file names, so no package import can reach that directory,
and a path insert is the only way in.

Every metric test states its expected number and says in a comment how the
number was counted by hand. The repository baseline test pins the four counts
from update_plan.md, because the gate's limits were calibrated against them.

Set MGCP_QUALITY_METRICS_DIR to a directory holding a modified copy of the
module to run this suite against that copy. Each assertion here was proven to
fail against a broken copy that way, which is the only evidence that a test
bites.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_DIR = REPO_ROOT / "src" / "mgcp" / "hook_templates"
QM_DIR = Path(os.environ.get("MGCP_QUALITY_METRICS_DIR", str(HOOK_DIR)))

sys.path.insert(0, str(QM_DIR))

import quality_metrics as qm  # noqa: E402

CHECKS = ["swallowed_error", "pass_through_wrapper"]

# The file-length ratchet is a separate metric from the per-function ones. These
# tests raise it out of the way so a 26-line fixture does not report a file
# violation while a cyclomatic case is under test.
NO_FILE_LIMIT = {"file_lines": 10_000}


def func(source: str, name: str = "f") -> dict:
    """The metric dict for one function in a source string."""
    return qm.measure_source(source, "m.py")["functions"][name]


def branchy(name: str, branches: int) -> str:
    """Source for a function whose cyclomatic complexity is 1 + ``branches``.

    Each branch is one ``if`` at the top level of the body, so the count is
    readable from the argument alone.
    """
    lines = [f"def {name}(a):"]
    for i in range(branches):
        lines.append(f"    if a == {i}:")
        lines.append("        pass")
    lines.append("    return a")
    return "\n".join(lines) + "\n"


def numbered_file(line_count: int) -> str:
    """Source of exactly ``line_count`` lines and no functions."""
    return "\n".join(["x = 1"] * line_count) + "\n"


# ---------------------------------------------------------------------------
# One metric at a time, against a hand count
# ---------------------------------------------------------------------------

CYCLOMATIC_FIXTURE = '''
def f(a, b):
    if a:
        for x in b:
            pass
    while a:
        break
    try:
        pass
    except ValueError:
        pass
    return [y for y in b]
'''


def test_cyclomatic_counts_each_branch_point():
    """Guards the branch-point set: if, for, while, except and comprehension.

    Hand count on CYCLOMATIC_FIXTURE: base 1, plus if, for, while, the one
    except handler and the one comprehension. That is 1 + 5 = 6.
    """
    assert func(CYCLOMATIC_FIXTURE)["cyclomatic"] == 6


def test_length_is_the_inclusive_line_span():
    """Guards length as end_lineno - lineno + 1, so the def line counts.

    Hand count: the def line, two assignments and the return. That is 4.
    """
    source = "def f():\n    x = 1\n    y = 2\n    return x + y\n"
    assert func(source)["length"] == 4


def test_depth_counts_the_deepest_block():
    """Guards nesting depth as the deepest block chain, try and with included.

    Hand count on the first source: if is 1, for is 2, while is 3, so 3. A
    function with no blocks is 0. On the third source: with is 1 and try is 2,
    and the except handler adds nothing because a handler is not a new block.
    """
    deep = "def f(a):\n    if a:\n        for x in a:\n            while x:\n                pass\n"
    assert func(deep)["depth"] == 3

    flat = "def f(a):\n    return a + 1\n"
    assert func(flat)["depth"] == 0

    with_try = (
        "def f(a):\n"
        "    with open(a) as fh:\n"
        "        try:\n"
        "            return fh.read()\n"
        "        except OSError:\n"
        "            return ''\n"
    )
    assert func(with_try)["depth"] == 2


def test_params_counts_positional_only_normal_and_keyword_only():
    """Guards the parameter count across all three parameter kinds.

    Hand count: a and b are positional-only, c and d are normal, e and g are
    keyword-only. That is 2 + 2 + 2 = 6. *args and **kwargs are not counted,
    because they are one parameter each at the call site and the metric is
    about how many separate things a caller must supply.
    """
    source = "def f(a, b, /, c, d, *, e, g):\n    pass\n"
    assert func(source)["params"] == 6

    starred = "def f(a, *args, **kwargs):\n    pass\n"
    assert func(starred)["params"] == 1


# ---------------------------------------------------------------------------
# The definition pin
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression,expected",
    [
        # Base 1 plus one operator. Counting per operand would give 3.
        ("a and b", 2),
        # Base 1 plus two operators in one BoolOp of three operands. Counting
        # per operand would give 4.
        ("a and b and c", 3),
        ("a or b or c or d", 4),
        # Three BoolOp nodes: the outer and, plus two inner or. One operator
        # each, plus base 1. Counting per operand would give 7.
        ("(a or b) and (c or d)", 4),
    ],
)
def test_cyclomatic_counts_one_per_boolean_operator(expression, expected):
    """Pins boolean counting to len(values) - 1, one per operator.

    This is the definition the limits rest on. Counting one per operand instead
    moves this repository from 58 functions over cyclomatic 10 to 66, and 10 was
    calibrated against 58. The repository test below is the other half of the
    pin, and this one names the rule it depends on.
    """
    source = f"def f(a, b, c, d):\n    return {expression}\n"
    assert func(source)["cyclomatic"] == expected


def _git_read(args: list[str]) -> str:
    """Read-only git output from this repository, or "" when git cannot answer."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=30
    )
    return result.stdout if result.returncode == 0 else ""


def test_repository_baseline_counts_hold():
    """Pins the four src/ counts the gate limits were calibrated against.

    update_plan.md measured 463 functions in src/ at HEAD: 58 over cyclomatic
    10, 31 over length 80, 19 over depth 4 and 5 over params 6. A change to any
    metric definition moves these numbers, and a silent move would retune all
    five gates at once. Counting one per boolean operand rather than per
    operator moves the first number to 66 on its own.

    The committed tree is the corpus, not the working tree. Several agents edit
    src/ at the same time in this repository, and an unfinished edit open in
    someone else's buffer moved this count from 58 to 65 while these tests were
    written. The calibration was done on committed code, so the pin reads
    committed code.

    quality_metrics.py is excluded because measuring the ruler with itself lets
    a definition change hide inside its own result.
    """
    listing = _git_read(["ls-tree", "-r", "--name-only", "HEAD", "src"])
    if not listing:
        pytest.skip("no committed HEAD to measure; the baseline is a property of committed code")

    over = {"cyclomatic": 0, "length": 0, "depth": 0, "params": 0}
    limits = {"cyclomatic": 10, "length": 80, "depth": 4, "params": 6}
    counted = 0

    for path in sorted(listing.splitlines()):
        if not path.endswith(".py") or path.endswith("/quality_metrics.py"):
            continue
        measured = qm.measure_source(_git_read(["show", f"HEAD:{path}"]), path)
        for metrics in measured.get("functions", {}).values():
            counted += 1
            for metric, limit in limits.items():
                if metrics[metric] > limit:
                    over[metric] += 1

    # Deliberately NOT an equality assertion on the counts. The first version
    # pinned 463 functions and 58/31/19/5, and the very commit that added the
    # gate moved them to 519 and 57/30/19/5, because the refactor the gate
    # forced reduced two of them. A pin that fails on every commit teaches
    # people to edit the number, which is the opposite of a guard.
    #
    # What must not drift is the DEFINITION, so that is what is asserted: the
    # shape of the distribution, and the operand-versus-operator rule that the
    # limits were calibrated on.
    assert counted > 400, f"only {counted} functions measured; the walk is broken"
    assert 40 <= over["cyclomatic"] <= 90, over
    assert 20 <= over["length"] <= 60, over
    assert over["depth"] < 40, over
    assert over["params"] < 20, over

    # The load-bearing half. Cyclomatic complexity counts one per boolean
    # OPERATOR. Counting per operand instead raises the over-limit count by
    # roughly a sixth, and the limits were set against the operator figure.
    per_operand = _count_over_with_operand_counting(listing, limits["cyclomatic"])
    assert per_operand > over["cyclomatic"], (
        "counting one per boolean operand no longer differs from counting one "
        "per operator, so the definition this test exists to pin has changed"
    )


def _count_over_with_operand_counting(listing: str, limit: int) -> int:
    """How many functions exceed the limit if boolean OPERANDS are counted.

    The alternative reading of "1 + boolean operands", which the plan's prose
    allowed and its baseline table did not. Measured here rather than asserted
    as a number, so the comparison stays true as the codebase changes.
    """
    import ast

    total = 0
    for path in sorted(listing.splitlines()):
        if not path.endswith(".py") or path.endswith("/quality_metrics.py"):
            continue
        try:
            tree = ast.parse(_git_read(["show", f"HEAD:{path}"]))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            score = qm._cyclomatic(node)
            # One extra per BoolOp turns per-operator into per-operand.
            score += sum(1 for n in ast.walk(node) if isinstance(n, ast.BoolOp))
            if score > limit:
                total += 1
    return total


def test_nested_def_resets_depth():
    """Guards that a nested def starts its own depth instead of adding to the outer one.

    Hand count: outer has one if, so depth 1. The def inside that if does not
    make its body depth 2 and 3; inner owns its if and its for, so inner is 2.
    Accumulating instead would report outer at 3 and push every closure-using
    function over the depth limit.
    """
    source = (
        "def outer(a):\n"
        "    if a:\n"
        "        def inner(b):\n"
        "            if b:\n"
        "                for c in b:\n"
        "                    pass\n"
        "        return inner\n"
    )
    functions = qm.measure_source(source, "m.py")["functions"]
    assert functions["outer"]["depth"] == 1
    assert functions["outer.inner"]["depth"] == 2


# ---------------------------------------------------------------------------
# Unparseable source
# ---------------------------------------------------------------------------


def test_measure_source_returns_empty_for_unparseable_source():
    """Guards the fail-open path for a file with a syntax error.

    The ratchet compares two versions of a file. A syntax error on either side
    makes the comparison meaningless, so the caller needs a falsy result it can
    skip the file on, not an exception that takes the hook down.
    """
    assert qm.measure_source("def f(:\n") == {}
    assert qm.measure_source("if True\n    pass\n") == {}
    assert qm.measure_source("x = 1\n") != {}


def test_banned_patterns_returns_empty_for_unparseable_source():
    """Guards the same fail-open path in the banned-pattern pass.

    Both passes run on the same staged blob. If one raised while the other
    returned, a commit holding a syntax error would be refused by the gate
    rather than by the test suite, which reports the error far better.
    """
    assert qm.banned_patterns("def f(:\n", CHECKS) == []
    assert qm.banned_patterns("try:\n    x = 1\nexcept Exception:\n    pass\n", CHECKS) != []


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def test_new_function_over_a_limit_is_a_violation():
    """Guards the rule that new code must meet the limits.

    branchy("f", 12) has cyclomatic 13 against a limit of 10. It is absent from
    before, so it is new, and new code has no legacy claim.
    """
    violations = qm.compare({}, qm.measure_source(branchy("f", 12), "m.py"), NO_FILE_LIMIT)
    assert [(v["name"], v["metric"], v["before"], v["after"]) for v in violations] == [
        ("f", "cyclomatic", None, 13)
    ]


def test_unchanged_legacy_function_over_a_limit_is_allowed():
    """Guards the ratchet itself: existing debt does not block a commit.

    58 functions in src/ are already over cyclomatic 10. A fixed limit would
    refuse every commit that touches server.py or init_project.py, so a function
    at 13 on both sides reports nothing.
    """
    before = qm.measure_source(branchy("f", 12), "m.py")
    after = qm.measure_source(branchy("f", 12), "m.py")
    assert qm.compare(before, after, NO_FILE_LIMIT) == []


def test_legacy_function_that_gets_worse_is_a_violation():
    """Guards the one thing the ratchet does forbid: over the limit and rising.

    13 to 15 against a limit of 10. The function was already failing, and this
    commit makes it fail harder.
    """
    before = qm.measure_source(branchy("f", 12), "m.py")
    after = qm.measure_source(branchy("f", 14), "m.py")
    violations = qm.compare(before, after, NO_FILE_LIMIT)
    assert len(violations) == 1
    assert (violations[0]["metric"], violations[0]["before"], violations[0]["after"]) == (
        "cyclomatic",
        13,
        15,
    )
    assert violations[0]["reason"] == "already over the limit and got worse"


def test_legacy_function_that_improves_while_still_over_the_limit_is_allowed():
    """Guards the exit from legacy debt: partial progress must not be refused.

    15 down to 13 against a limit of 10 is still over, and reporting it would
    make the only allowed move a full rewrite. Then nobody would move at all.
    """
    before = qm.measure_source(branchy("f", 14), "m.py")
    after = qm.measure_source(branchy("f", 12), "m.py")
    assert qm.compare(before, after, NO_FILE_LIMIT) == []


def test_function_crossing_a_limit_for_the_first_time_is_a_violation():
    """Guards the boundary case: a function going from under to over.

    9 to 12 against a limit of 10. The before value is under the limit, so the
    rise alone is not what makes this a violation; crossing is.
    """
    before = qm.measure_source(branchy("f", 8), "m.py")
    after = qm.measure_source(branchy("f", 11), "m.py")
    violations = qm.compare(before, after, NO_FILE_LIMIT)
    assert len(violations) == 1
    assert (violations[0]["before"], violations[0]["after"]) == (9, 12)
    assert violations[0]["reason"] == "crossed the limit"

    # A rise that stays under the limit is not a violation, or every commit
    # that adds one branch anywhere would be refused.
    under = qm.compare(
        qm.measure_source(branchy("f", 3), "m.py"),
        qm.measure_source(branchy("f", 5), "m.py"),
        NO_FILE_LIMIT,
    )
    assert under == []


def test_empty_before_treats_every_function_as_new():
    """Guards the new-file and rename case, where before is {}.

    A rename reads as a delete plus an add, so the renamed function is held to
    the full limits. That is the intent: renaming a long function is the
    cheapest moment to split it. The same function compared against itself
    reports nothing, which is what makes this test about the empty before and
    not about the function.
    """
    after = qm.measure_source(branchy("f", 12) + branchy("g", 2), "m.py")

    assert qm.compare(after, after, NO_FILE_LIMIT) == []

    violations = qm.compare({}, after, NO_FILE_LIMIT)
    assert [v["name"] for v in violations] == ["f"]
    assert violations[0]["before"] is None
    assert violations[0]["reason"] == "new function starts over the limit"


def test_params_metric_is_skipped_for_an_mcp_tool():
    """Guards the exemption for a function whose parameters are a wire schema.

    An MCP tool's parameter list is the schema the model fills in. Seven fields
    is seven fields, not a function to split, so params does not apply. Without
    the decorator the same seven parameters are a violation, which is how this
    test shows the decorator is doing the work.
    """
    tool = "@mcp.tool()\ndef t(a, b, c, d, e, g, h):\n    return 1\n"
    plain = "def t(a, b, c, d, e, g, h):\n    return 1\n"

    assert qm.compare({}, qm.measure_source(tool, "m.py"), NO_FILE_LIMIT) == []

    violations = qm.compare({}, qm.measure_source(plain, "m.py"), NO_FILE_LIMIT)
    assert [(v["metric"], v["after"]) for v in violations] == [("params", 7)]


@pytest.mark.parametrize(
    "before_lines,after_lines,expect_violation",
    [
        # Under the limit on both sides, so growth is free.
        (2, 4, False),
        # Over the limit and grew by two lines.
        (6, 8, True),
        # Over the limit and shrank. Net growth is what the ratchet measures,
        # so a file that is still too long but getting shorter passes.
        (8, 7, False),
        # Over the limit and unchanged.
        (8, 8, False),
        # Crossed the limit in this commit.
        (3, 8, True),
    ],
)
def test_file_lines_ratchets_on_net_growth_once_over_the_limit(
    before_lines, after_lines, expect_violation
):
    """Guards the file-length ratchet: net growth, and only once over the limit.

    server.py is 3,399 lines against a limit of 1,000. A line-for-line limit
    would refuse every new MCP tool, so the rule is that an over-limit file may
    not grow net. Removing 40 lines and adding 38 is allowed.
    """
    limits = {"file_lines": 5}
    before = qm.measure_source(numbered_file(before_lines), "m.py")
    after = qm.measure_source(numbered_file(after_lines), "m.py")
    violations = [v for v in qm.compare(before, after, limits) if v["metric"] == "file_lines"]

    assert bool(violations) is expect_violation
    if expect_violation:
        assert (violations[0]["before"], violations[0]["after"]) == (before_lines, after_lines)


def test_new_file_over_the_file_length_limit_is_a_violation():
    """Guards the new-file half of the file-length ratchet.

    A new file has no prior line count to ratchet against, so the limit applies
    outright. Otherwise a 3,000-line new module would land unchecked.
    """
    violations = qm.compare({}, qm.measure_source(numbered_file(8), "m.py"), {"file_lines": 5})
    assert [(v["metric"], v["before"], v["reason"]) for v in violations] == [
        ("file_lines", None, "new file over the limit")
    ]


# ---------------------------------------------------------------------------
# Banned patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    x = 1\nexcept Exception:\n    pass\n",
        "try:\n    x = 1\nexcept:\n    pass\n",
        "try:\n    x = 1\nexcept BaseException:\n    pass\n",
        "for i in range(3):\n    try:\n        x = 1\n    except Exception:\n        continue\n",
        "def f():\n    try:\n        return 1\n    except Exception:\n        return None\n",
        "def f():\n    try:\n        return 1\n    except Exception:\n        return False\n",
    ],
)
def test_broad_except_that_discards_the_error_is_reported(source):
    """Guards detection of the swallowed-error pattern.

    A broad except whose whole body is pass, continue or a return of a constant
    turns a failure into a wrong answer with no log line. That is the shape the
    check looks for, across all three discarding bodies.
    """
    found = qm.banned_patterns(source, ["swallowed_error"])
    assert [f["pattern"] for f in found] == ["swallowed_error"]


@pytest.mark.parametrize(
    "source",
    [
        # Logs the error, so the failure is still visible.
        "import logging\ntry:\n    x = 1\nexcept Exception:\n    logging.exception('x')\n",
        # Re-raises, so nothing is swallowed.
        "try:\n    x = 1\nexcept Exception:\n    raise\n",
        # Narrow, so the author named the failure they expect.
        "try:\n    x = 1\nexcept ValueError:\n    pass\n",
        # Returns a computed value rather than a constant.
        "def f(a):\n    try:\n        return 1\n    except Exception:\n        return a.default\n",
    ],
)
def test_broad_except_that_handles_the_error_is_not_reported(source):
    """Guards against the check firing on handlers that do their job.

    A handler that logs, re-raises, names its exception or returns a computed
    value is doing work. Reporting those would make the rule noise, and a noisy
    gate gets bypassed as a habit.
    """
    assert qm.banned_patterns(source, ["swallowed_error"]) == []


def test_allow_comment_suppresses_the_broad_except_report():
    """Guards the documented escape hatch for the hook's own fail-open handlers.

    The PreToolUse hook must allow a tool call on any parse error, which means
    broad excepts with empty bodies in the hook itself. A rule that refuses the
    code implementing its own fail-open invariant could never ship.
    """
    swallowing = "try:\n    x = 1\nexcept Exception:\n    pass\n"
    allowed = (
        "try:\n    x = 1\n"
        "except Exception:  # mgcp: allow-broad-except the hook fails open\n    pass\n"
    )
    assert qm.banned_patterns(swallowing, ["swallowed_error"]) != []
    assert qm.banned_patterns(allowed, ["swallowed_error"]) == []


@pytest.mark.parametrize(
    "source",
    [
        "def w(a, b):\n    return inner(a, b)\n",
        # A docstring is dropped before the body is measured, so a documented
        # wrapper is still a wrapper.
        "def w(a, b):\n    '''Forward to inner.'''\n    return inner(a, b)\n",
        "def w(a, b):\n    return mod.inner(a, b)\n",
    ],
)
def test_wrapper_that_only_forwards_its_parameters_is_reported(source):
    """Guards detection of the pass-through wrapper pattern.

    A function whose body is one return of a call taking its own parameters in
    its own order adds a name and a stack frame and nothing else. The deleted
    package copy of the enforcement evaluator was reached through two of these.
    """
    found = qm.banned_patterns(source, ["pass_through_wrapper"])
    assert [f["pattern"] for f in found] == ["pass_through_wrapper"]
    assert "w only forwards its parameters" in found[0]["detail"]


@pytest.mark.parametrize(
    "source",
    [
        # Reorders, so the wrapper owns the call signature.
        "def w(a, b):\n    return inner(b, a)\n",
        # Forwards a name that is not its own parameter.
        "def w(a, b):\n    return inner(a, other)\n",
        # Adds a keyword argument the caller cannot see.
        "def w(a, b):\n    return inner(a, b, strict=True)\n",
        # Forwards a subset.
        "def w(a, b):\n    return inner(a)\n",
        # Unpacks rather than forwards.
        "def w(a, b):\n    return inner(*a, **b)\n",
        # Takes no parameters, so there is nothing to forward.
        "def w():\n    return inner()\n",
        # Does work before the call.
        "def w(a, b):\n    a = a + 1\n    return inner(a, b)\n",
        # The call result is not what is returned.
        "def w(a, b):\n    return inner(a, b) or None\n",
    ],
)
def test_wrapper_that_does_work_is_not_reported(source):
    """Guards against the check firing on a wrapper that changes the call.

    Reordering, renaming, dropping a parameter, adding a keyword or unpacking
    all mean the wrapper is the thing deciding how inner is called. Only the
    identity case is reported.
    """
    assert qm.banned_patterns(source, ["pass_through_wrapper"]) == []


def test_signature_default_does_not_exempt_a_pass_through_wrapper():
    """Pins what happens when the wrapper defaults one of its own parameters.

    update_plan.md lists a default as an exemption, and the module docstring
    repeats it, but the check compares forwarded names against parameter names
    and never reads the default. So this wrapper is reported. The behaviour is
    pinned here rather than left undefined, because the deny reason names the
    function and a reader needs to know which rule produced it.
    """
    source = "def w(a, b=2):\n    return inner(a, b)\n"
    assert [f["pattern"] for f in qm.banned_patterns(source, ["pass_through_wrapper"])] == [
        "pass_through_wrapper"
    ]


# ---------------------------------------------------------------------------
# Path exclusion
# ---------------------------------------------------------------------------


def test_double_star_glob_matches_one_segment_and_many():
    """Guards the default exclude globs across both path depths.

    Every rule in update_plan.md excludes "tests/**", because writing tests is
    never what should blow the diff budget. The gate needs that to cover
    "tests/x.py" and "tests/a/b.py" alike, and to leave "src/" alone.

    fnmatch treats ** as a plain * that crosses a path separator, so the first
    fnmatch call already matches both depths. The module's extra "tests/*"
    fallback is therefore unreachable on CPython. Removing it keeps this test
    green, so the test guards the contract and not that one branch.
    """
    assert qm.excluded("tests/x.py", ["tests/**"]) is True
    assert qm.excluded("tests/a/b.py", ["tests/**"]) is True
    assert qm.excluded("src/mgcp/server.py", ["tests/**"]) is False
    assert qm.excluded("src/mgcp/server.py", []) is False


# ---------------------------------------------------------------------------
# Script entry
# ---------------------------------------------------------------------------


def test_report_exits_zero(tmp_path, capsys):
    """Guards --report as information rather than a gate.

    The Validation table reads the over-limit count from --report every week. A
    non-zero exit there would fail a CI job that is only meant to print a number.
    """
    (tmp_path / "sample.py").write_text(branchy("f", 12))

    assert qm.main(["--report", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 functions" in out
    assert "cyclomatic" in out


def test_base_with_an_unresolvable_ref_exits_non_zero(tmp_path, capsys):
    """Guards against a CI pass that measured nothing.

    A shallow clone often has no origin/main, and the old failure mode of a
    gate like this one is to find no base, compare nothing and report success.
    The temp repository has a .py file, so an empty repository is not what makes
    this exit non-zero. The bad ref is.
    """
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "sample.py").write_text(branchy("f", 12))

    assert qm.main(["--base", "no-such-ref-xyz", "--cwd", str(tmp_path)]) != 0
    assert "no-such-ref-xyz" in capsys.readouterr().err


def _commit(path: Path, message: str) -> None:
    """A commit that does not depend on the machine's git identity."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "-C", str(path), "commit", "-qm", message],
                   check=True, env=env)


def test_an_untracked_new_file_is_measured(tmp_path, capsys):
    """A file git does not track yet still faces the limits.

    A new file is the one case where the limits apply in full, because there is
    no base version to ratchet against. git diff cannot see an untracked file,
    so leaving them out printed "no Python changed" for code the gate had never
    read. That is how a module above the limit reached a commit here: it was
    measured before being staged, and the clean result was about nothing.
    """
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "tracked.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _commit(tmp_path, "base")

    (tmp_path / "fresh.py").write_text(branchy("f", 16))
    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "fresh.py" in out
    assert "new function starts over the limit" in out


def test_an_untracked_file_under_the_limit_still_passes(tmp_path, capsys):
    """Seeing untracked files must not turn every new file into a violation."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "tracked.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _commit(tmp_path, "base")

    (tmp_path / "fresh.py").write_text(branchy("f", 3))
    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 0
    assert "no violations" in capsys.readouterr().out


def test_the_ruler_passes_its_own_limits():
    """quality_metrics.py meets the limits it enforces.

    New code must meet the limits, and this module was new. It shipped with four
    functions above them, including compare at 24 cyclomatic complexity against
    a limit of 10. The CLI could not catch it, because the file was untracked
    when it was measured.
    """
    source = Path(qm.__file__).read_text()
    measured = qm.measure_source(source, "quality_metrics.py")
    limits = {k: v for k, v in qm.DEFAULT_LIMITS.items() if k != "file_lines"}

    over = {
        name: {m: f[m] for m, limit in limits.items() if f[m] > limit}
        for name, f in measured["functions"].items()
    }
    over = {name: bad for name, bad in over.items() if bad}
    assert over == {}, f"the ruler exceeds its own limits: {over}"
    assert qm.banned_patterns(source, list(qm.BANNED_CHECKS)) == []


def test_the_cli_looks_for_banned_patterns(tmp_path, capsys):
    """The CLI and the hook must run the same banned-pattern checks.

    The CLI passed the METRIC label names as the check list, so every lookup
    compared "swallowed_error" against "cyclomatic" and matched nothing. CI runs
    the CLI and the hook reads its own rule, so the two callers of one module
    disagreed and CI never ran these checks at all.
    """
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "keep.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _commit(tmp_path, "base")

    (tmp_path / "keep.py").write_text(
        "def f():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 1
    assert "swallowed_error" in capsys.readouterr().out


def test_the_escape_comment_still_exempts_a_fail_open(tmp_path, capsys):
    """A handler that states its reason is allowed.

    The hook's own fail-open handlers need this escape, and a check that refused
    the code implementing fail-open would be self-defeating.
    """
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "keep.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _commit(tmp_path, "base")

    (tmp_path / "keep.py").write_text(
        "def f():\n"
        "    try:\n"
        "        return 1\n"
        f"    except Exception:  # {qm.ALLOW_BROAD_EXCEPT} measured, on purpose\n"
        "        pass\n"
    )
    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 0
    assert "no violations" in capsys.readouterr().out


def test_the_enforcing_hook_has_no_unexplained_swallowed_errors():
    """Every fail-open in the gate states why it is one.

    The gate is allowed to discard errors, because it allows a call it cannot
    measure. It is not allowed to do so silently, since a swallowed error there
    is an unaudited allow. This caught three handlers once the CLI started
    running the check it had been passing the wrong argument for.
    """
    hook = HOOK_DIR / "pre-tool-dispatcher.py"
    found = qm.banned_patterns(hook.read_text(), list(qm.BANNED_CHECKS))
    assert found == [], f"unexplained swallowed errors at {[f['lineno'] for f in found]}"


EXEMPT_SOURCE = '''def build(a):  # mgcp: allow-size a build script is one ordered sequence
    if a == 1:
        return 1
    if a == 2:
        return 2
    if a == 3:
        return 3
    if a == 4:
        return 4
    if a == 5:
        return 5
    if a == 6:
        return 6
    if a == 7:
        return 7
    if a == 8:
        return 8
    if a == 9:
        return 9
    if a == 10:
        return 10
    if a == 11:
        return 11
    return None
'''


def test_a_declared_large_function_may_grow():
    """The point of the escape: a build script can gain another branch.

    Some work is honestly one long sequence. Without this, adding a ninth
    supported client to an installer's argparse dispatch would be refused for
    taking cyclomatic complexity from 59 to 60, which is a refusal with nothing
    wrong behind it.
    """
    before = qm.measure_source(EXEMPT_SOURCE, "build.py")
    grown = EXEMPT_SOURCE.replace(
        "    return None\n", "    if a == 12:\n        return 12\n    return None\n")
    after = qm.measure_source(grown, "build.py")

    assert after["functions"]["build"]["cyclomatic"] > \
        before["functions"]["build"]["cyclomatic"]
    assert qm.compare(before, after) == []


def test_the_same_growth_is_refused_without_the_marker():
    """The exemption is what permits it, not the shape of the code."""
    plain = EXEMPT_SOURCE.replace(
        "  # mgcp: allow-size a build script is one ordered sequence", "")
    before = qm.measure_source(plain, "build.py")
    grown = plain.replace(
        "    return None\n", "    if a == 12:\n        return 12\n    return None\n")
    found = qm.compare(before, qm.measure_source(grown, "build.py"))
    assert [v["metric"] for v in found] == ["cyclomatic"]


def test_a_marker_without_a_reason_exempts_nothing():
    """An exemption nobody justified is a hole, so a bare marker does not count."""
    bare = EXEMPT_SOURCE.replace(
        "# mgcp: allow-size a build script is one ordered sequence",
        "# mgcp: allow-size")
    measured = qm.measure_source(bare, "build.py")
    assert measured["functions"]["build"]["size_exempt"] == ""

    grown = bare.replace(
        "    return None\n", "    if a == 12:\n        return 12\n    return None\n")
    assert qm.compare(measured, qm.measure_source(grown, "build.py")) != []


def test_the_marker_is_read_from_the_line_above_the_def():
    """A signature long enough to need the exemption has no room for it."""
    source = (
        "# mgcp: allow-size an installer is one ordered sequence\n"
        + EXEMPT_SOURCE.replace(
            "  # mgcp: allow-size a build script is one ordered sequence", "")
    )
    measured = qm.measure_source(source, "build.py")
    assert measured["functions"]["build"]["size_exempt"].startswith("an installer")


def test_the_declared_exemptions_are_the_ones_we_chose():
    """The exemption list is a decision, so it is pinned.

    Three functions in init_project.py are declared deliberately large: a CLI
    entry point and two install sequences, all flat dispatch at depth 4. The
    deeply nested functions in server.py are NOT exempt, because depth 10 in 94
    lines is not one ordered sequence by any reading.
    """
    exempt = {}
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        for name, f in qm.measure_source(
                path.read_text(), str(path)).get("functions", {}).items():
            if f.get("size_exempt"):
                exempt[f"{path.relative_to(REPO_ROOT)}:{name}"] = f["size_exempt"]

    assert sorted(exempt) == [
        "src/mgcp/init_project.py:init_claude_hooks",
        "src/mgcp/init_project.py:init_global_hooks",
        "src/mgcp/init_project.py:main",
    ], sorted(exempt)
    assert all(len(reason) > 20 for reason in exempt.values())


SWALLOWED = (
    "def f():\n"
    "    try:\n"
    "        return 1\n"
    "    except Exception:\n"
    "        pass\n"
)


def _repo_with(tmp_path, body):
    """A repository whose committed keep.py holds `body`."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "keep.py").write_text(body)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    _commit(tmp_path, "base")


def test_a_pre_existing_banned_pattern_does_not_block_an_unrelated_change(
    tmp_path, capsys
):
    """The ratchet's own rule, which this check used to break.

    Every hit in the whole file was reported, so an `except: pass` that predated
    the gate refused any commit that touched its file. In this project an atexit
    handler with two of them blocked a change to a table definition 800 lines
    away. A gate that refuses work the author did not do is a gate that gets
    bypassed, and the first bypass is permanent.
    """
    _repo_with(tmp_path, SWALLOWED)
    (tmp_path / "keep.py").write_text(SWALLOWED + "\nUNRELATED = 2\n")

    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 0
    assert "no violations" in capsys.readouterr().out


def test_adding_one_more_of_the_same_pattern_is_still_refused(tmp_path, capsys):
    """The pair to the test above. Held at its count, not forgiven."""
    _repo_with(tmp_path, SWALLOWED)
    (tmp_path / "keep.py").write_text(SWALLOWED + SWALLOWED.replace("def f", "def g"))

    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "swallowed_error" in out
    assert "1 -> 2" in out, "the report has to say it went up, not just that it is there"
    # Counts alone do not say WHERE, and the anchor line is the first hit.
    assert "every hit is at line 4, 9" in out


def test_removing_a_banned_pattern_is_not_a_violation(tmp_path, capsys):
    _repo_with(tmp_path, SWALLOWED + SWALLOWED.replace("def f", "def g"))
    (tmp_path / "keep.py").write_text(SWALLOWED)

    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 0


def test_a_new_file_has_every_pattern_counted(tmp_path, capsys):
    """A file with no base version has nothing to be held at."""
    _repo_with(tmp_path, "x = 1\n")
    (tmp_path / "fresh.py").write_text(SWALLOWED)

    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 1
    assert "swallowed_error" in capsys.readouterr().out


def test_moving_a_banned_pattern_down_the_file_is_not_an_addition(tmp_path, capsys):
    """Why the comparison counts per pattern instead of matching line numbers.

    Any edit above a hit moves its line number, so a line-keyed comparison
    would read an untouched `except: pass` as new on every commit.
    """
    _repo_with(tmp_path, SWALLOWED)
    (tmp_path / "keep.py").write_text("HEADER = 1\n" * 30 + SWALLOWED)

    assert qm.main(["--base", "HEAD", "--cwd", str(tmp_path)]) == 0
