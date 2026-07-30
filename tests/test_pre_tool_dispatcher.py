"""Tests for pre-tool-dispatcher.py — the PreToolUse generic enforcement hook.

The hook is stdlib-only (no mgcp import) and reads rules from
$MGCP_ENFORCEMENT_CONFIG (or ~/.mgcp/enforcement_rules.json). These tests
drive it as a subprocess with a temp config + state file.

See tests/test_enforcement.py for unit tests of the schema-backed
evaluator module that the MCP tools use — the two share semantics.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).parent.parent
    / "src"
    / "mgcp"
    / "hook_templates"
    / "pre-tool-dispatcher.py"
)


GIT_GATE_RULE = {
    "name": "git-requires-query-lessons",
    "description": "",
    "enabled": True,
    "trigger": {
        "tool_name": "Bash",
        "command_match": {
            "type": "git_subcommand",
            "subcommands": ["commit", "push"],
            "pattern": "",
        },
    },
    "preconditions": [
        {
            "type": "tool_called_this_turn",
            "tool_name": "mcp__mgcp__query_lessons",
            "couplings": [],
        },
    ],
    "bypass_scope": "git",
    "deny_reason": "git commit/push requires query_lessons first",
}


@pytest.fixture(scope="module")
def hook_module():
    spec = importlib.util.spec_from_file_location("pre_tool_dispatcher", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCommandDetector:
    """Quote-aware tokenizer must distinguish `git commit` the command
    from `git commit` as a string inside an argument."""

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m foo",
            "git push",
            "git push origin main",
            "make build && git push origin main",
            "run_tests.sh; git commit -am 'ok'",
            "(cd subdir && git commit -m msg)",
            # A newline separates commands just as `&&` does, but shlex with
            # whitespace_split consumes it, which left `git` looking like an
            # argument to the previous command instead of a command start.
            "cd /repo\ngit commit -m foo",
            "echo starting\ngit push origin main",
            "cd /a\ncd /b\n\ngit commit -am x",
            "git -C /repo commit -m x",
            "git -c user.name=x commit -m y",
            "git --git-dir=/r/.git push origin main",
        ],
    )
    def test_matches_real_git_invocations(self, hook_module, command):
        assert hook_module._detect_git_subcommand(command, ["commit", "push"]) is True

    @pytest.mark.parametrize(
        "command",
        [
            "grep -r 'git commit' docs/",
            'echo "how to git commit properly" > guide.txt',
            "cat README.md | grep 'git push'",
            "git status",
            "git log --oneline",
            "python3 train.py",
            "echo git commit",
            "echo hi\npython3 train.py",
            "cd /repo\ngit status",
            "git -C /repo status",
        ],
    )
    def test_does_not_match_non_invocations(self, hook_module, command):
        assert hook_module._detect_git_subcommand(command, ["commit", "push"]) is False

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'oops",
            "git commit -F - <<'MSG'\nthe project's fix\nMSG",
            "cd /repo\ngit commit -F - <<'M'\ndon't\nM",
        ],
    )
    def test_unparseable_command_fails_closed(self, hook_module, command):
        """An unterminated quote is author-controlled text, not proof of
        safety. Failing open here made every git gate optional for anyone
        who wrote an apostrophe in a commit message."""
        assert hook_module._detect_git_subcommand(command, ["commit", "push"]) is True

    @pytest.mark.parametrize(
        "command",
        ["echo don't", "grep -r can't src/", "python -c \"print('unclosed\""],
    )
    def test_failing_closed_stays_scoped_to_git(self, hook_module, command):
        assert hook_module._detect_git_subcommand(command, ["commit", "push"]) is False


class TestApologyDetector:
    @pytest.mark.parametrize(
        "text",
        [
            "sorry about that",
            "My bad, I missed it.",
            "you're right — that was wrong.",
            "You are right, I should have asked.",
            "That was my mistake.",
            "my apologies for the confusion",
            "My apology — let me fix it.",
            "I apologize for the delay.",
            "Apologise, rerunning now.",
        ],
    )
    def test_apology_text_matches(self, hook_module, text):
        assert hook_module._has_apology(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Here is the plan.",
            "Running the tests now.",
            "The right answer is 42.",
            "No mistake was made.",
            "",
        ],
    )
    def test_non_apology_text_does_not_match(self, hook_module, text):
        assert hook_module._has_apology(text) is False

    def test_current_turn_extracts_only_since_last_user_message(self, hook_module, tmp_path):
        path = tmp_path / "transcript.jsonl"
        entries = [
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "OLD TURN sorry"}]}},
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "NEW TURN hello"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "more of new turn"}]}},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries))
        text = hook_module._current_turn_assistant_text(str(path))
        assert "NEW TURN" in text
        assert "more of new turn" in text
        assert "OLD TURN" not in text

    def test_current_turn_handles_missing_file(self, hook_module, tmp_path):
        assert hook_module._current_turn_assistant_text(str(tmp_path / "nope.jsonl")) == ""

    def test_current_turn_handles_empty_path(self, hook_module):
        assert hook_module._current_turn_assistant_text("") == ""


class TestEnforcement:
    """End-to-end: run the hook as a subprocess against a temp
    enforcement_rules.json + workflow_state.json."""

    def _run(
        self,
        hook_input: dict,
        state: dict,
        tmp_path: Path,
        rules: list | None = None,
    ):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state))

        rules_file = tmp_path / "enforcement_rules.json"
        rules_file.write_text(
            json.dumps({"version": 1, "rules": rules if rules is not None else [GIT_GATE_RULE]})
        )

        env = {
            "MGCP_STATE_FILE": str(state_file),
            "MGCP_ENFORCEMENT_CONFIG": str(rules_file),
            # The hook writes gate_audit.jsonl under MGCP_DATA_DIR. An
            # explicit env dict inherits nothing, so without this the hook
            # subprocess would write into the operator's live ~/.mgcp.
            "MGCP_DATA_DIR": str(tmp_path),
            "PATH": "/usr/bin:/bin",
        }
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            env=env,
        )
        return result

    def test_git_commit_without_query_lessons_is_denied(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
        )
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_git_commit_after_query_lessons_is_allowed(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_tools_called": ["mcp__mgcp__query_lessons"], "turn_bypass_scopes": []},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_scoped_bypass_allows_through(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            {"turn_tools_called": [], "turn_bypass_scopes": ["git"]},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_star_bypass_allows_through(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            {"turn_tools_called": [], "turn_bypass_scopes": ["*"]},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_unrelated_scope_does_not_bypass(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}},
            {"turn_tools_called": [], "turn_bypass_scopes": ["docs"]},
            tmp_path,
        )
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_non_bash_tool_is_allowed(self, tmp_path):
        r = self._run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "x", "old_string": "git commit", "new_string": "y"},
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
        )
        assert r.stdout.strip() == ""

    def test_missing_state_file_fails_open_to_deny(self, tmp_path):
        # No state file -> empty state -> git rule fires and denies.
        state_file = tmp_path / "nope.json"
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps({"version": 1, "rules": [GIT_GATE_RULE]}))
        r = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
            ),
            capture_output=True,
            text=True,
            env={
                "MGCP_STATE_FILE": str(state_file),
                "MGCP_ENFORCEMENT_CONFIG": str(rules_file),
                "MGCP_DATA_DIR": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        )
        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_missing_rules_file_fails_open(self, tmp_path):
        # No rules file -> no enforcement -> any tool call allowed.
        r = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(
                {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
            ),
            capture_output=True,
            text=True,
            env={
                "MGCP_STATE_FILE": str(tmp_path / "nope.json"),
                "MGCP_ENFORCEMENT_CONFIG": str(tmp_path / "does-not-exist.json"),
                "MGCP_DATA_DIR": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        )
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_disabled_rule_is_skipped(self, tmp_path):
        disabled = dict(GIT_GATE_RULE, enabled=False)
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[disabled],
        )
        assert r.stdout.strip() == ""

    def _make_transcript(self, tmp_path: Path, assistant_text: str) -> Path:
        """Build a minimal transcript JSONL: one user entry then one
        assistant entry with the given text. Returns the file path."""
        path = tmp_path / "transcript.jsonl"
        lines = [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                },
            },
        ]
        path.write_text("\n".join(json.dumps(l) for l in lines))
        return path

    def test_apology_denies_non_add_lesson_tool(self, tmp_path):
        transcript = self._make_transcript(
            tmp_path, "sorry, you're right about the qdrant lock issue."
        )
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
                "transcript_path": str(transcript),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[],
        )
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "apology-requires-add-lesson" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_apology_allows_add_lesson_through(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, my mistake.")
        r = self._run(
            {
                "tool_name": "mcp__mgcp__add_lesson",
                "tool_input": {"id": "x", "trigger": "y", "action": "z"},
                "transcript_path": str(transcript),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_apology_satisfied_after_add_lesson_called(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "my bad, you were right.")
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
                "transcript_path": str(transcript),
            },
            {
                "turn_tools_called": ["mcp__mgcp__add_lesson"],
                "turn_bypass_scopes": [],
            },
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_apology_bypass_scope_allows_through(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry about that.")
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
                "transcript_path": str(transcript),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": ["apology"]},
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_non_apology_assistant_text_does_not_fire(self, tmp_path):
        transcript = self._make_transcript(
            tmp_path, "Here is the plan: read the file, edit it, commit."
        )
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
                "transcript_path": str(transcript),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_apology_in_previous_turn_does_not_fire(self, tmp_path):
        # Apology was before a user message — current turn is clean.
        path = tmp_path / "transcript.jsonl"
        entries = [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "sorry, my mistake!"}],
                },
            },
            {"type": "user", "message": {"role": "user", "content": "ok continue"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "proceeding with the plan."}],
                },
            },
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries))
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
                "transcript_path": str(path),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_apology_with_missing_transcript_fails_open(self, tmp_path):
        r = self._run(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
                "transcript_path": str(tmp_path / "nope.jsonl"),
            },
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path,
            rules=[],
        )
        assert r.stdout.strip() == ""

    def test_malformed_hook_input_fails_open(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="not valid json{{{",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


class TestApologyGateExitsAndAudit:
    """v2.11 (reduced): the gate keeps v2.9's seven patterns and adds only
    what survived adversarial review — a second exit, an exemption for the
    discovery calls needed to REACH the exits, and an audit line per event.

    The cut machinery is deliberate. A denial counter with an advisory-degrade
    valve was built, measured, and removed: it short-circuited every OTHER
    enforcement rule and crashed the hook open on a malformed value. A
    quote-stripper and first-person sentence window were built and removed
    too: the apostrophe in "you're right" made the canonical trigger stop
    firing. Simpler is what passed.
    """

    _make_transcript = TestEnforcement._make_transcript
    _run = TestEnforcement._run

    def _audit(self, tmp_path):
        p = tmp_path / "gate_audit.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_discovery_tools_are_never_gated(self, tmp_path):
        """ToolSearch loads add_lesson's schema. Gating it gates the exit."""
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        for tool in ("ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"):
            r = self._run(
                {"tool_name": tool, "tool_input": {}, "transcript_path": str(transcript)},
                {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
            assert r.stdout.strip() == "", f"{tool} must never be denied"

    def test_discovery_allowlist_is_exact_not_prefix(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        r = self._run(
            {"tool_name": "ToolSearcher", "tool_input": {}, "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_adjudicate_tool_is_permitted_while_armed(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        r = self._run(
            {"tool_name": "mcp__mgcp__adjudicate_apology_gate", "tool_input": {},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        assert r.stdout.strip() == ""

    def test_canonical_v29_triggers_still_arm(self, tmp_path):
        """The reduction must not weaken detection. 'you're right' is the
        case the deleted quote-stripper broke."""
        for text in ("sorry, that was wrong", "you're right, I broke it",
                     "my mistake entirely", "I apologize for the confusion"):
            transcript = self._make_transcript(tmp_path, text)
            r = self._run(
                {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
                 "transcript_path": str(transcript)},
                {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
            assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", text

    def test_deny_names_both_exits_and_writes_audit(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, that broke.")
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "S1"},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "mcp__mgcp__add_lesson" in reason and "mcp__mgcp__adjudicate_apology_gate" in reason
        e = self._audit(tmp_path)[-1]
        assert (e["event"], e["gate"], e["session_id"]) == ("deny", "apology", "S1")

    def test_comply_writes_audit_with_lesson_id(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, that broke.")
        r = self._run(
            {"tool_name": "mcp__mgcp__add_lesson", "tool_input": {"id": "captured"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        assert r.stdout.strip() == ""
        e = self._audit(tmp_path)[-1]
        assert e["event"] == "comply" and e["lesson_id"] == "captured"

    def test_rule_denials_are_audited_too(self, tmp_path):
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=None)
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        e = self._audit(tmp_path)[-1]
        assert e["event"] == "deny" and e["gate"] == "rules" and e["rules"]

    def test_data_rules_still_enforced_when_the_gate_is_armed(self, tmp_path):
        """The deleted escape valve short-circuited every data rule. Nothing
        the apology gate does may switch the rest of enforcement off."""
        transcript = self._make_transcript(tmp_path, "sorry, that broke.")
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": ["mcp__mgcp__add_lesson"], "turn_bypass_scopes": []},
            tmp_path, rules=None)
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "git rule must still fire once the apology gate is satisfied"

    def test_adjudication_does_not_leak_across_sessions(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        state = {"turn_tools_called": [], "turn_bypass_scopes": [],
                 "turn_apology_adjudication": {"verdict": "not_apology",
                                               "session_id": "session-A"}}
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "session-B"},
            state, tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "session-A"},
            state, tmp_path, rules=[])
        assert r.stdout.strip() == ""

    def test_apology_verdict_does_not_open_the_gate(self, tmp_path):
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "A"},
            {"turn_tools_called": [], "turn_bypass_scopes": [],
             "turn_apology_adjudication": {"verdict": "apology", "session_id": "A"}},
            tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_state_does_not_crash_the_hook(self, tmp_path):
        """A crash means rc=1 and empty stdout, which the harness reads as
        allow — a silent, unaudited bypass. The deleted counter did exactly
        that on a malformed value."""
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        for bad in ("oops", 42, ["a"], {"verdict": None}):
            r = self._run(
                {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
                 "transcript_path": str(transcript)},
                {"turn_tools_called": [], "turn_bypass_scopes": [],
                 "turn_apology_adjudication": bad}, tmp_path, rules=[])
            assert r.returncode == 0, f"hook crashed on {bad!r}: {r.stderr}"
            assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_non_dict_state_file_does_not_disable_enforcement(self, tmp_path):
        """A valid-JSON non-object (array, string, number) parses cleanly and
        then raises on .get(), crashing the hook PAST the rule loop — which
        the harness reads as allow. Malformed state must degrade to 'no
        state', never to 'no enforcement'."""
        state_file = tmp_path / "state.json"
        rules_file = tmp_path / "enforcement_rules.json"
        rules_file.write_text(json.dumps({"version": 1, "rules": [GIT_GATE_RULE]}))
        for raw in ("[1,2]", '"a string"', "42", "not json{{{", ""):
            state_file.write_text(raw)
            r = subprocess.run(
                [sys.executable, str(HOOK_PATH)],
                input=json.dumps({"tool_name": "Bash",
                                  "tool_input": {"command": "git commit -m x"}}),
                capture_output=True, text=True,
                env={"MGCP_STATE_FILE": str(state_file),
                     "MGCP_ENFORCEMENT_CONFIG": str(rules_file),
                     "MGCP_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
            )
            assert r.returncode == 0, f"crashed on state={raw!r}: {r.stderr[:200]}"
            assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", \
                f"git rule not enforced with state={raw!r}"

    def test_adjudication_session_match_is_exact(self, tmp_path):
        """A malformed session_id must not normalise to '' and thereby match
        every caller — type confusion into a global gate-opener."""
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        for bad_session in (5, None, ["A"], {"s": 1}):
            r = self._run(
                {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
                 "transcript_path": str(transcript), "session_id": "S1"},
                {"turn_tools_called": [], "turn_bypass_scopes": [],
                 "turn_apology_adjudication": {"verdict": "not_apology",
                                               "session_id": bad_session}},
                tmp_path, rules=[])
            assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", \
                f"malformed session_id {bad_session!r} opened the gate"

    def test_sessionless_adjudication_is_legacy_scoped(self, tmp_path):
        """Both sides sessionless -> match (required where the harness omits
        session_id). Adjudication sessionless, caller identified -> no match."""
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        state = {"turn_tools_called": [], "turn_bypass_scopes": [],
                 "turn_apology_adjudication": {"verdict": "not_apology"}}
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript)}, state, tmp_path, rules=[])
        assert r.stdout.strip() == "", "sessionless pair must match"
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "S1"},
            state, tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_apology_bypass_does_not_disable_data_rules(self, tmp_path):
        """Scoped bypass means scoped. The deleted escape valve failed this."""
        transcript = self._make_transcript(tmp_path, "sorry, I broke it.")
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": ["apology"]},
            tmp_path, rules=None)
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
