"""Tests for src/mgcp/enforcement.py — the rule schema and its persistence.

This module owns the Pydantic schema, DEFAULT_RULES and load/save only. The
evaluator lives in the stdlib-only hook and nowhere else; its semantics are
covered by tests/test_pre_tool_dispatcher.py, which exercises the code that
actually runs.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mgcp.enforcement import (
    CommandMatch,
    EnforcementConfig,
    EnforcementRule,
    Precondition,
    Trigger,
    default_config,
    load_config,
    save_config,
)


class TestSchema:
    def test_default_config_is_valid_and_named(self):
        cfg = default_config()
        assert cfg.version == 1
        assert cfg.rules, "default_config must ship at least one rule"
        assert cfg.rules[0].name == "git-requires-query-lessons"

    def test_rule_roundtrips_through_model_dump(self):
        rule = EnforcementRule(
            name="r",
            trigger=Trigger(
                tool_name="Bash",
                command_match=CommandMatch(type="git_subcommand", subcommands=["push"]),
            ),
            preconditions=[Precondition(type="tool_called_this_turn", tool_name="t")],
            bypass_scope="git",
            deny_reason="because",
        )
        again = EnforcementRule.model_validate(rule.model_dump())
        assert again == rule

    def test_unknown_command_match_type_is_rejected(self):
        with pytest.raises(ValidationError):
            CommandMatch(type="not_a_type", pattern="x")

    def test_unknown_precondition_type_is_rejected(self):
        with pytest.raises(ValidationError):
            Precondition(type="not_a_type")

    def test_every_default_rule_names_a_bypass_scope(self):
        # A rule with no scope can only be bypassed by disabling everything.
        for rule in default_config().rules:
            assert rule.bypass_scope, f"{rule.name} has no bypass_scope"


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "rules.json"
        save_config(default_config(), p)
        loaded = load_config(p)
        assert len(loaded.rules) == len(default_config().rules)
        assert loaded.rules[0].name == "git-requires-query-lessons"

    def test_missing_file_returns_default(self, tmp_path):
        # A missing file is a fresh install; the defaults ARE the truth there.
        cfg = load_config(tmp_path / "nope.json")
        assert len(cfg.rules) >= 1

    def test_corrupt_file_raises_rather_than_substituting_defaults(self, tmp_path):
        # Returning defaults here is how a write tool silently overwrites the
        # rules the hook is still enforcing out of this very file.
        p = tmp_path / "bad.json"
        p.write_text("not json{{{")
        with pytest.raises(json.JSONDecodeError):
            load_config(p)

    def test_invalid_schema_raises_rather_than_substituting_defaults(self, tmp_path):
        p = tmp_path / "invalid.json"
        p.write_text(json.dumps({"version": 1, "rules": [{"nope": True}]}))
        with pytest.raises(ValidationError):
            load_config(p)

    def test_a_present_file_is_never_silently_replaced(self, tmp_path):
        # The regression this guards: load -> mutate -> save, where the load
        # failed open, wipes a file the hook can still partially read.
        p = tmp_path / "rules.json"
        save_config(
            EnforcementConfig(
                version=1,
                rules=[
                    EnforcementRule(
                        name="user-authored",
                        trigger=Trigger(tool_name="Edit"),
                        preconditions=[],
                        bypass_scope="docs",
                        deny_reason="keep me",
                    )
                ],
            ),
            p,
        )
        assert [r.name for r in load_config(p).rules] == ["user-authored"]


def test_prose_style_rule_ships_enabled_and_narrow():
    """The commit prose rule must be a shipped default, not a local edit.

    The style rule has to apply in every project on the machine, and a rule that
    only exists in one operator's enforcement_rules.json does not survive a fresh
    install. This also pins the check to one character. A wider pattern would
    start refusing legitimate commits, and a rule people switch off enforces
    nothing.
    """
    from mgcp.enforcement import DEFAULT_RULES

    rule = next((r for r in DEFAULT_RULES if r.name == "commit-message-prose-style"), None)
    assert rule is not None, "commit-message-prose-style is missing from DEFAULT_RULES"
    assert rule.enabled is True, "the prose rule ships enabled"
    assert rule.trigger.tool_name == "Bash"
    assert rule.trigger.command_match.subcommands == ["commit"], (
        "push carries no message, so the rule belongs on commit only"
    )
    assert len(rule.preconditions) == 1
    pre = rule.preconditions[0]
    assert pre.type == "tool_input_glob"
    assert pre.field == "command", "the message arrives inside the Bash command"
    assert pre.deny_globs == ["—".join(["*", "*"])], (
        f"the check must stay one character, found {pre.deny_globs}"
    )
    assert rule.bypass_scope == "prose"
    # The message has to teach the rule, because it is the only place the agent
    # reads at the moment it is blocked.
    for expected in ("ASD-STE100", "Google", "MGCP_BYPASS:prose"):
        assert expected in rule.deny_reason, f"deny_reason does not mention {expected}"


def test_no_default_rule_message_contains_an_em_dash():
    """The rules that teach the style must follow it."""
    from mgcp.enforcement import DEFAULT_RULES

    for rule in DEFAULT_RULES:
        for field in ("description", "deny_reason"):
            text = getattr(rule, field) or ""
            assert "—" not in text, f"{rule.name}.{field} contains an em dash"


class TestMergeMissingDefaults:
    """Delivery of shipped rules to an install that already has a rules file.

    Nothing else does this. load_config returns the defaults only when the file
    is MISSING, and the one other writer seeds on first install behind a check
    for a missing file, so an operator who installed a week ago never receives a
    rule shipped since. update_plan.md named mgcp-bootstrap as the channel and
    bootstrap has no enforcement code at all.
    """

    @staticmethod
    def _existing(tmp_path, rules):
        path = tmp_path / "enforcement_rules.json"
        path.write_text(json.dumps({"version": 1, "rules": rules}))
        return path

    @staticmethod
    def _custom(name, enabled=True):
        return {
            "name": name, "description": "the operator wrote this",
            "enabled": enabled,
            "trigger": {"tool_name": "Bash", "tool_names": [],
                        "command_match": {"type": "contains", "subcommands": [],
                                          "pattern": "rm -rf"}},
            "preconditions": [{"type": "tool_called_this_turn",
                               "tool_name": "mcp__mgcp__query_lessons",
                               "couplings": [], "field": "", "deny_globs": [],
                               "patterns": [], "exclude_globs": [],
                               "max_added_lines": 0, "max_files": 0, "limits": {},
                               "banned": [], "file_length_exempt": [],
                               "min_added_lines": 0, "pattern": "",
                               "input_match": {}, "when_staged_added": []}],
            "bypass_scope": "mine", "deny_reason": "mine", "mode": "enforce",
        }

    def test_it_adds_only_what_is_absent(self, tmp_path):
        from mgcp.enforcement import DEFAULT_RULES, STRUCTURE_RULES, merge_missing_defaults

        path = self._existing(tmp_path, [self._custom("my-own-rule")])
        result = merge_missing_defaults(path)
        expected = len(DEFAULT_RULES) + len(STRUCTURE_RULES)
        assert len(result["added"]) == expected
        assert result["kept"] == 1

    def test_a_rule_already_present_is_never_touched(self, tmp_path):
        """A populated file is a customised file.

        A shipped rule may be disabled, retuned or renamed on purpose, and
        overwriting it by name would silently revert that.
        """
        from mgcp.enforcement import DEFAULT_RULES, merge_missing_defaults

        shipped = DEFAULT_RULES[0].model_dump()
        shipped["enabled"] = False                 # the operator turned it off
        shipped["deny_reason"] = "my own wording"  # and rewrote the message
        path = self._existing(tmp_path, [shipped])

        merge_missing_defaults(path)
        after = json.loads(path.read_text())["rules"]
        assert after[0]["enabled"] is False
        assert after[0]["deny_reason"] == "my own wording"
        assert after[0]["name"] == DEFAULT_RULES[0].name

    def test_existing_rules_keep_their_order_and_stay_first(self, tmp_path):
        from mgcp.enforcement import merge_missing_defaults

        names = ["alpha", "beta", "gamma"]
        path = self._existing(tmp_path, [self._custom(n) for n in names])
        merge_missing_defaults(path)
        after = [r["name"] for r in json.loads(path.read_text())["rules"]]
        assert after[:3] == names

    def test_it_is_idempotent(self, tmp_path):
        from mgcp.enforcement import merge_missing_defaults

        path = self._existing(tmp_path, [self._custom("mine")])
        first = merge_missing_defaults(path)
        assert first["added"]
        second = merge_missing_defaults(path)
        assert second["added"] == []

    def test_every_structure_rule_ships_in_audit_mode(self, tmp_path):
        """A wrong limit must cost a log row, not a blocked session."""
        from mgcp.enforcement import STRUCTURE_RULES

        for rule in STRUCTURE_RULES:
            assert rule.mode == "audit", rule.name
            assert rule.enabled is False, rule.name

    def test_audit_rules_stay_off_when_the_deployed_hook_is_old(
        self, tmp_path, monkeypatch
    ):
        """mode is a key an older hook has never heard of.

        An unknown key there is neither a parse error nor a fail-open: it is
        ignored and the rule enforces. That is the state after a git pull and
        before mgcp-init, so an audit rule must not be enabled then.
        """
        import mgcp.enforcement as enf

        monkeypatch.setattr(enf, "_deployed_hook_is_current",
                            lambda: (False, "installed payload is 2.17"))
        path = self._existing(tmp_path, [self._custom("mine")])
        result = enf.merge_missing_defaults(path)
        assert result["enabled"] == []
        by_name = {r["name"]: r for r in json.loads(path.read_text())["rules"]}
        # Only the audit-mode structure rules stay off. The four original gates
        # are established enforcing rules and arrive enabled, which is correct.
        for rule in enf.STRUCTURE_RULES:
            assert by_name[rule.name]["enabled"] is False, rule.name
        assert "2.17" in result["hook_detail"]

    def test_audit_rules_turn_on_when_the_hook_is_current(self, tmp_path, monkeypatch):
        """A disabled rule records nothing, so it would never be promoted.

        Once the installed hook honours audit mode, enabling is safe: the rule
        refuses nothing and writes the would_deny rows the promotion decision
        needs.
        """
        import mgcp.enforcement as enf

        monkeypatch.setattr(enf, "_deployed_hook_is_current",
                            lambda: (True, "payload matches"))
        path = self._existing(tmp_path, [self._custom("mine")])
        result = enf.merge_missing_defaults(path)
        assert set(result["enabled"]) == {r.name for r in enf.STRUCTURE_RULES}

        by_name = {r["name"]: r for r in json.loads(path.read_text())["rules"]}
        for rule in enf.STRUCTURE_RULES:
            assert by_name[rule.name]["enabled"] is True
            assert by_name[rule.name]["mode"] == "audit"
        # The operator's own rule is untouched either way.
        assert by_name["mine"]["enabled"] is True

    def test_a_file_that_does_not_parse_is_never_overwritten(self, tmp_path):
        path = tmp_path / "enforcement_rules.json"
        path.write_text("{not json")
        from mgcp.enforcement import merge_missing_defaults

        with pytest.raises(Exception):
            merge_missing_defaults(path)
        assert path.read_text() == "{not json"
