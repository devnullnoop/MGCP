"""Tests for src/mgcp/enforcement.py — the generic rule evaluator.

The same evaluator semantics are re-implemented in the stdlib-only hook
(src/mgcp/hook_templates/pre-tool-dispatcher.py). tests/test_pre_tool_dispatcher.py
covers the hook; this file covers the Pydantic-schema-backed module that
the MCP tools use.
"""
from __future__ import annotations

import json

import pytest

from mgcp.enforcement import (
    BYPASS_ALL,
    CommandMatch,
    EnforcementConfig,
    EnforcementRule,
    Precondition,
    Trigger,
    check_coupling,
    default_config,
    detect_git_subcommand,
    evaluate_precondition,
    evaluate_rules,
    load_config,
    parse_bypass_scopes,
    save_config,
    trigger_matches,
)


class TestGitSubcommandDetector:
    @pytest.mark.parametrize(
        "command,expected",
        [
            ("git commit -m x", "commit"),
            ("git push origin main", "push"),
            ("make build && git push", "push"),
            ("(cd sub && git commit -am x)", "commit"),
            ("grep 'git commit' docs/", None),
            ('echo "how to git commit" > f', None),
            ("git status", None),
            ("python train.py", None),
        ],
    )
    def test_detection(self, command, expected):
        assert detect_git_subcommand(command, ["commit", "push"]) == expected

    def test_unterminated_quote_fails_open(self):
        assert detect_git_subcommand("git commit -m 'oops", ["commit"]) is None


class TestTriggerMatches:
    def test_wildcard_tool_matches_any(self):
        trig = Trigger(tool_name="*")
        assert trigger_matches(trig, "Edit", {}) is True
        assert trigger_matches(trig, "Bash", {"command": "ls"}) is True

    def test_exact_tool_matches(self):
        trig = Trigger(tool_name="Edit")
        assert trigger_matches(trig, "Edit", {}) is True
        assert trigger_matches(trig, "Write", {}) is False

    def test_command_match_requires_bash(self):
        trig = Trigger(
            tool_name="*",
            command_match=CommandMatch(type="contains", pattern="git"),
        )
        # command_match on non-Bash is a no-match (we can't read a command)
        assert trigger_matches(trig, "Edit", {"command": "git"}) is False

    def test_git_subcommand(self):
        trig = Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="git_subcommand", subcommands=["push"]),
        )
        assert trigger_matches(trig, "Bash", {"command": "git push"}) is True
        assert trigger_matches(trig, "Bash", {"command": "git commit"}) is False

    def test_regex(self):
        trig = Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="regex", pattern=r"rm\s+-rf"),
        )
        assert trigger_matches(trig, "Bash", {"command": "rm -rf /"}) is True
        assert trigger_matches(trig, "Bash", {"command": "ls"}) is False

    def test_contains(self):
        trig = Trigger(
            tool_name="Bash",
            command_match=CommandMatch(type="contains", pattern="sudo"),
        )
        assert trigger_matches(trig, "Bash", {"command": "sudo apt"}) is True
        assert trigger_matches(trig, "Bash", {"command": "apt"}) is False


class TestPreconditions:
    def test_tool_called_this_turn(self):
        pre = Precondition(type="tool_called_this_turn", tool_name="foo")
        ok, _ = evaluate_precondition(pre, {"turn_tools_called": ["foo"]}, [])
        assert ok is True
        ok, _ = evaluate_precondition(pre, {"turn_tools_called": []}, [])
        assert ok is False

    def test_tool_not_called_this_turn(self):
        pre = Precondition(type="tool_not_called_this_turn", tool_name="bad")
        ok, _ = evaluate_precondition(pre, {"turn_tools_called": ["good"]}, [])
        assert ok is True
        ok, _ = evaluate_precondition(pre, {"turn_tools_called": ["bad"]}, [])
        assert ok is False

    def test_staged_files_coupling_satisfied(self):
        pre = Precondition(
            type="staged_files_coupling",
            couplings=[{"when_staged": ["src/*.py"], "require_one_of": ["README.md"]}],
        )
        ok, _ = evaluate_precondition(pre, {}, ["src/foo.py", "README.md"])
        assert ok is True

    def test_staged_files_coupling_violated(self):
        pre = Precondition(
            type="staged_files_coupling",
            couplings=[{"when_staged": ["src/*.py"], "require_one_of": ["README.md"]}],
        )
        ok, detail = evaluate_precondition(pre, {}, ["src/foo.py"])
        assert ok is False
        assert "src/foo.py" in detail

    def test_staged_files_coupling_no_trigger(self):
        pre = Precondition(
            type="staged_files_coupling",
            couplings=[{"when_staged": ["src/*.py"], "require_one_of": ["README.md"]}],
        )
        # No staged file matches when_staged -> coupling doesn't apply
        ok, _ = evaluate_precondition(pre, {}, ["docs/foo.md"])
        assert ok is True


class TestCheckCoupling:
    def test_empty_staged(self):
        ok, trig = check_coupling([], ["a"], ["b"])
        assert ok is True and trig == []

    def test_when_hit_req_hit(self):
        ok, trig = check_coupling(["src/x.py", "README.md"], ["src/*.py"], ["README.md"])
        assert ok is True and "src/x.py" in trig

    def test_when_hit_req_miss(self):
        ok, trig = check_coupling(["src/x.py"], ["src/*.py"], ["README.md"])
        assert ok is False and "src/x.py" in trig


class TestEvaluateRules:
    def _rule(self, **kw):
        base = dict(
            name="r",
            trigger=Trigger(tool_name="Bash"),
            preconditions=[Precondition(type="tool_called_this_turn", tool_name="x")],
            bypass_scope="scope",
            deny_reason="nope",
        )
        base.update(kw)
        return EnforcementRule(**base)

    def test_disabled_rule_skipped(self):
        cfg = EnforcementConfig(rules=[self._rule(enabled=False)])
        v = evaluate_rules(cfg, "Bash", {}, {"turn_tools_called": []}, ".")
        assert v == []

    def test_bypass_scope_skipped(self):
        cfg = EnforcementConfig(rules=[self._rule()])
        v = evaluate_rules(cfg, "Bash", {}, {"turn_tools_called": []}, ".", {"scope"})
        assert v == []

    def test_bypass_all_skipped(self):
        cfg = EnforcementConfig(rules=[self._rule()])
        v = evaluate_rules(cfg, "Bash", {}, {"turn_tools_called": []}, ".", {BYPASS_ALL})
        assert v == []

    def test_untriggered_rule_skipped(self):
        cfg = EnforcementConfig(rules=[self._rule()])
        v = evaluate_rules(cfg, "Edit", {}, {"turn_tools_called": []}, ".")
        assert v == []

    def test_precondition_unsatisfied_yields_violation(self):
        cfg = EnforcementConfig(rules=[self._rule()])
        v = evaluate_rules(cfg, "Bash", {}, {"turn_tools_called": []}, ".")
        assert len(v) == 1
        assert v[0].rule_name == "r"
        assert v[0].bypass_scope == "scope"

    def test_precondition_satisfied(self):
        cfg = EnforcementConfig(rules=[self._rule()])
        v = evaluate_rules(cfg, "Bash", {}, {"turn_tools_called": ["x"]}, ".")
        assert v == []


class TestBypassParser:
    def test_bare_token(self):
        assert parse_bypass_scopes("hello MGCP_BYPASS world") == ["*"]

    def test_scoped_token(self):
        assert parse_bypass_scopes("MGCP_BYPASS:git") == ["git"]

    def test_multiple(self):
        out = parse_bypass_scopes("MGCP_BYPASS:git and MGCP_BYPASS:docs")
        assert out == ["git", "docs"]

    def test_case_insensitive(self):
        assert parse_bypass_scopes("mgcp_bypass:Git") == ["Git"]

    def test_none(self):
        assert parse_bypass_scopes("just a normal prompt") == []


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "rules.json"
        save_config(default_config(), p)
        loaded = load_config(p)
        assert len(loaded.rules) == len(default_config().rules)
        assert loaded.rules[0].name == "git-requires-query-lessons"

    def test_missing_file_returns_default(self, tmp_path):
        p = tmp_path / "nope.json"
        cfg = load_config(p)
        assert len(cfg.rules) >= 1

    def test_corrupt_file_returns_default(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json{{{")
        cfg = load_config(p)
        assert len(cfg.rules) >= 1

    def test_invalid_schema_returns_default(self, tmp_path):
        p = tmp_path / "invalid.json"
        p.write_text(json.dumps({"version": 1, "rules": [{"nope": True}]}))
        cfg = load_config(p)
        # Pydantic validation failure -> default fallback
        assert len(cfg.rules) >= 1
