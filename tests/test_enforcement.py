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
