"""Tests for src/mgcp/enforcement.py — the rule schema and its persistence.

This module owns the Pydantic schema, DEFAULT_RULES and load/save only. The
evaluator lives in the stdlib-only hook and nowhere else; its semantics are
covered by tests/test_pre_tool_dispatcher.py, which exercises the code that
actually runs.
"""
from __future__ import annotations

import json
import re

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
                               "max_net_lines": 0, "max_files": 0, "limits": {},
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

    def test_a_structure_rule_in_audit_mode_ships_disabled(self, tmp_path):
        """A wrong limit must cost a log row, not a blocked session.

        A promoted rule is the other case, and it ships enabled and enforcing
        on the evidence of its own audit rows. Both groups are asserted to be
        non-empty, so this cannot pass by one of them having no members.
        """
        from mgcp.enforcement import STRUCTURE_RULES

        audit = [r for r in STRUCTURE_RULES if r.mode == "audit"]
        enforcing = [r for r in STRUCTURE_RULES if r.mode == "enforce"]
        assert audit, "no structure rule is still in audit mode"
        assert enforcing, "no structure rule has been promoted"
        for rule in audit:
            assert rule.enabled is False, rule.name
        for rule in enforcing:
            assert rule.enabled is True, rule.name

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
        # Only the audit-mode structure rules stay off. Every established
        # enforcing rule arrives enabled, which is correct: an old hook that
        # ignores `mode` enforces, and enforcing is what they ask for.
        for rule in enf.STRUCTURE_RULES:
            want = rule.mode == "enforce"
            assert by_name[rule.name]["enabled"] is want, rule.name
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
        audit = [r for r in enf.STRUCTURE_RULES if r.mode == "audit"]
        assert set(result["enabled"]) == {r.name for r in audit}

        by_name = {r["name"]: r for r in json.loads(path.read_text())["rules"]}
        for rule in audit:
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


class TestLoadConfigMigration:
    """An older saved file still parses.

    `save_config` writes every schema field, so a file saved before a field was
    removed carries that key on every precondition and `extra="forbid"` rejects
    it. Dropping a removed key on load is what keeps the file loading, and a
    file that does not load takes every enforcement tool and the dashboard with
    it. Retired rules go the same way: `merge_missing_defaults` adds by name and
    never removes, so nothing else can take one out.
    """

    def _written(self, tmp_path, rules):
        path = tmp_path / "enforcement_rules.json"
        path.write_text(json.dumps({"version": 1, "rules": rules}))
        return path

    def _budget_rule(self, name, extra_key=True):
        pre = {"type": "diff_budget", "max_files": 8}
        if extra_key:
            pre["max_added_lines"] = 300
        return {"name": name, "enabled": True, "trigger": {"tool_name": "Bash"},
                "preconditions": [pre], "bypass_scope": "size",
                "deny_reason": "too big"}

    def test_a_removed_field_does_not_stop_the_file_loading(self, tmp_path):
        from mgcp.enforcement import load_config

        path = self._written(tmp_path, [self._budget_rule("mine")])
        config = load_config(path)
        assert [r.name for r in config.rules] == ["mine"]
        assert config.rules[0].preconditions[0].max_files == 8

    def test_a_retired_rule_is_dropped(self, tmp_path):
        from mgcp.enforcement import RETIRED_RULES, load_config

        assert "edit-diff-budget" in RETIRED_RULES
        path = self._written(tmp_path, [self._budget_rule("edit-diff-budget"),
                                        self._budget_rule("mine")])
        assert [r.name for r in load_config(path).rules] == ["mine"]

    def test_the_next_save_writes_the_cleaned_shape(self, tmp_path):
        from mgcp.enforcement import load_config, save_config

        path = self._written(tmp_path, [self._budget_rule("edit-diff-budget"),
                                        self._budget_rule("mine")])
        save_config(load_config(path), path)
        raw = json.loads(path.read_text())
        assert [r["name"] for r in raw["rules"]] == ["mine"]
        assert "max_added_lines" not in raw["rules"][0]["preconditions"][0]
        assert "max_net_lines" in raw["rules"][0]["preconditions"][0]

    def test_a_file_that_does_not_parse_still_raises(self, tmp_path):
        """The migration must not become a second fall-back to the defaults."""
        from mgcp.enforcement import load_config

        path = tmp_path / "enforcement_rules.json"
        path.write_text("{not json")
        with pytest.raises(Exception):
            load_config(path)

    def test_an_unknown_field_still_raises(self, tmp_path):
        """Only the named removed keys are dropped. A typo stays loud.

        `extra="forbid"` exists so a one-field typo fails instead of silently
        deleting enforcement on the next write. A blanket "drop what you do not
        know" migration would hand that back.
        """
        from mgcp.enforcement import load_config

        rule = self._budget_rule("mine", extra_key=False)
        rule["preconditions"][0]["max_nett_lines"] = 300
        with pytest.raises(Exception):
            load_config(self._written(tmp_path, [rule]))


class TestTheRatchetListsDoNotDrift:
    """CI and the hook must measure the same files with the same exemptions.

    ci.yml carries a comment saying these lists mirror the rule and that drift
    makes a commit pass the hook and fail the pull request "for a reason nobody
    can reproduce locally". Nothing checked it. This is that check: the comment
    asked for a test and did not have one.
    """

    def _ratchet_precondition(self):
        from mgcp.enforcement import STRUCTURE_RULES

        for rule in STRUCTURE_RULES:
            if rule.name == "commit-complexity-ratchet":
                return rule.preconditions[0]
        raise AssertionError("commit-complexity-ratchet is not a shipped rule")

    def _ci_flag(self, flag):
        """The quoted values the CI step passes to one flag."""
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1]
                / ".github" / "workflows" / "ci.yml").read_text()
        start = text.index(f"--{flag} ")
        # The argument list ends at the next flag or the end of the run block.
        tail = text[start + len(flag) + 3:]
        stop = min((i for i in (tail.find("--"), tail.find("\n\n"))
                    if i != -1), default=len(tail))
        return set(re.findall(r"'([^']+)'", tail[:stop]))

    def test_the_file_length_exemptions_match(self):
        rule = set(self._ratchet_precondition().file_length_exempt)
        assert rule, "the rule exempts nothing, so this test proves nothing"
        assert self._ci_flag("file-length-exempt") == rule

    def test_the_exclude_globs_match(self):
        rule = set(self._ratchet_precondition().exclude_globs)
        assert rule, "the rule excludes nothing, so this test proves nothing"
        assert self._ci_flag("exclude") == rule
