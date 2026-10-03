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
            # One word in front of the command used to move `git` off the
            # command boundary and out of the gate entirely -- no malformed
            # input required, just a path, a wrapper or an env assignment.
            "/usr/bin/git commit -m x",
            "./bin/git push",
            "env git commit -m x",
            "GIT_AUTHOR_NAME=x git commit -m y",
            "GIT_AUTHOR_NAME=x GIT_AUTHOR_EMAIL=y git commit -m z",
            "sudo git push",
            "time git push",
            "xargs git commit",
            "nohup /usr/bin/git push origin main",
            "cd /repo && sudo git commit -m x",
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
            # The wrapper/assignment allowance must not turn an ordinary
            # argument list into a command boundary.
            "echo --dry-run git commit",
            "grep git commit README.md",
            "sudo git status",
            "/usr/bin/git log",
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
            # The raw scan is the only detector left on this path, so it has
            # to see a path-invoked git too.
            "/usr/bin/git commit -m 'oops",
            "sudo git commit -m 'oops",
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
        assert hook_module._apology_match(text)[0] != ""

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
        assert hook_module._apology_match(text)[0] == ""

    @staticmethod
    def _write(path, entries):
        path.write_text("\n".join(json.dumps(e) for e in entries))
        return str(path)

    @staticmethod
    def _say(text):
        return {"type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def _call(name):
        return {"type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name}]}}

    def test_only_this_turn_counts(self, hook_module, tmp_path):
        """An apology before the last user prompt is a previous turn's business."""
        p = self._write(tmp_path / "t.jsonl", [
            self._say("OLD TURN sorry"),
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._say("NEW TURN hello"),
        ])
        assert hook_module._latest_apology(p) == ("", "", False)

    def test_an_apology_this_turn_is_found_unanswered(self, hook_module, tmp_path):
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._say("Sorry, my mistake."),
        ])
        pattern, sentence, answered = hook_module._latest_apology(p)
        assert pattern == r"\bsorry\b"
        assert sentence == "Sorry, my mistake."
        assert answered is False

    def test_add_lesson_after_the_apology_answers_it(self, hook_module, tmp_path):
        """This is what the gate accepts as payment, and the only thing."""
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._say("Sorry, my mistake."),
            self._call("mcp__mgcp__add_lesson"),
        ])
        pattern, _, answered = hook_module._latest_apology(p)
        assert pattern and answered is True

    def test_add_lesson_before_the_apology_does_not_answer_it(self, hook_module, tmp_path):
        """Order matters. A lesson written earlier does not pay for a later apology."""
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._call("mcp__mgcp__add_lesson"),
            self._say("Sorry, that was wrong."),
        ])
        pattern, _, answered = hook_module._latest_apology(p)
        assert pattern and answered is False

    def test_a_later_block_in_the_same_message_answers_it(self, hook_module, tmp_path):
        """An assistant turn can apologize and call add_lesson in one message."""
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Sorry, my mistake."},
                {"type": "tool_use", "name": "mcp__mgcp__add_lesson"},
            ]}},
        ])
        pattern, _, answered = hook_module._latest_apology(p)
        assert pattern and answered is True

    def test_another_tool_does_not_answer_it(self, hook_module, tmp_path):
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._say("Sorry, my mistake."),
            self._call("Bash"),
            self._call("mcp__mgcp__query_lessons"),
        ])
        pattern, _, answered = hook_module._latest_apology(p)
        assert pattern and answered is False

    def test_the_walk_passes_tool_result_user_entries(self, hook_module, tmp_path):
        """Tool results are type=='user' entries carrying tool_result blocks.

        Stopping at them would let any tool call, even a denied one, hide the
        apology and reopen the gate mid-turn.
        """
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            self._say("sorry, my mistake."),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "denied"}]}},
        ])
        assert hook_module._latest_apology(p)[0] != ""

    def test_a_text_block_user_prompt_still_ends_the_turn(self, hook_module, tmp_path):
        p = self._write(tmp_path / "t.jsonl", [
            self._say("OLD sorry"),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "new prompt"}]}},
            self._say("fresh turn, all clear here"),
        ])
        assert hook_module._latest_apology(p) == ("", "", False)

    def test_thinking_blocks_are_not_assistant_text(self, hook_module, tmp_path):
        """Reasoning the user never sees is not an apology to the user."""
        p = self._write(tmp_path / "t.jsonl", [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "sorry, I think I got that wrong"}]}},
        ])
        assert hook_module._latest_apology(p) == ("", "", False)

    def test_a_missing_file_matches_nothing(self, hook_module, tmp_path):
        assert hook_module._latest_apology(str(tmp_path / "nope.jsonl")) == ("", "", False)

    def test_an_empty_path_matches_nothing(self, hook_module):
        assert hook_module._latest_apology("") == ("", "", False)

    def test_a_corrupt_transcript_matches_nothing_and_does_not_raise(self, hook_module, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text("not json\n[]\n{\"type\": 7}\n\x00\n{broken")
        assert hook_module._latest_apology(str(p)) == ("", "", False)


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

    @staticmethod
    def _transcript_with_lesson(tmp_path, assistant_text, answered):
        """A one-turn transcript, optionally with add_lesson after the text."""
        entries = [
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant",
             "content": [{"type": "text", "text": assistant_text}]}},
        ]
        if answered:
            entries.append({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "tool_use", "name": "mcp__mgcp__add_lesson"}]}})
        path = tmp_path / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in entries))
        return path

    def test_apology_satisfied_once_the_lesson_is_in_the_transcript(self, tmp_path):
        transcript = self._transcript_with_lesson(tmp_path, "my bad, you were right.", True)
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo ok"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []},
            tmp_path, rules=[])
        assert r.stdout.strip() == "", r.stdout

    def test_the_state_file_cannot_open_the_gate(self, tmp_path):
        """turn_tools_called no longer answers "was this paid for".

        That list lives in workflow_state.json, which is one file for every
        project and every concurrent session, and which the gated agent can
        write. Claiming add_lesson there used to open the gate. The transcript
        decides now, and the transcript is not agent-writable.
        """
        transcript = self._transcript_with_lesson(tmp_path, "my bad, you were right.", False)
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo ok"},
             "transcript_path": str(transcript)},
            # Everything an attacker could put in the shared file.
            {"turn_tools_called": ["mcp__mgcp__add_lesson"],
             "turn_bypass_scopes": [],
             "turn_started_at": 9_999_999_999.0,
             "turn_session_id": "someone-elses-session"},
            tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny", (
            "the shared state file reopened the gate: " + r.stdout
        )

    def test_a_concurrent_session_cannot_disarm_the_gate(self, tmp_path):
        """The bypass an adversarial audit found, now closed.

        The gate used to bound its transcript walk by turn_started_at read from
        the shared state file. Any value newer than this session's own text
        made the walk stop at once, so the gate saw no text, allowed the call,
        and wrote nothing to gate_audit.jsonl. Another session's turn, a forged
        number and a clock correction all reached that silent bypass.
        """
        transcript = self._transcript_with_lesson(tmp_path, "Sorry, that was wrong.", False)
        for bound in (9_999_999_999.0, float("inf"), 1e18, -1, "not-a-number", None, True):
            r = self._run(
                {"tool_name": "Bash", "tool_input": {"command": "echo ok"},
                 "transcript_path": str(transcript), "session_id": "mine"},
                {"turn_tools_called": [], "turn_bypass_scopes": [],
                 "turn_started_at": bound, "turn_session_id": "other-session"},
                tmp_path, rules=[])
            assert r.returncode == 0, r.stderr
            decision = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]
            assert decision == "deny", f"turn_started_at={bound!r} disarmed the gate"

    def test_an_unanswered_apology_survives_a_mid_turn_message(self, tmp_path):
        """The mirror of the bug the timestamp bound was added to fix.

        A message sent mid-turn clears turn_tools_called and used to move the
        window past an apology that had NOT been answered, which let it escape.
        Reading the transcript fixes both directions at once: an answered
        apology reads as answered, and an unanswered one still denies.
        """
        transcript = self._transcript_with_lesson(tmp_path, "Sorry, that was wrong.", False)
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo ok"},
             "transcript_path": str(transcript)},
            # Exactly the state a mid-turn UserPromptSubmit leaves behind.
            {"turn_tools_called": [], "turn_bypass_scopes": [],
             "turn_started_at": 9_999_999_999.0},
            tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

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

    def test_a_denial_records_what_it_fired_on(self, tmp_path):
        """Without this the gate's precision cannot be computed from its log.

        A denial used to record only that the gate fired. The flagged sentence
        reached the audit record solely when the agent contested, so the agent
        chose the evidence for every record that had any: 39 denials with no
        sentence, 16 contests with one, all 16 self-ruled not_apology. Judging
        the gate needs the text it matched, and only the deny path has it.
        """
        transcript = self._make_transcript(
            tmp_path,
            "The suite is green. Sorry, that table was wrong. Moving on now.",
        )
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript), "session_id": "S1"},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

        e = self._audit(tmp_path)[-1]
        assert e["pattern"] == r"\bsorry\b"
        # The offending sentence, not the whole turn: a reader can judge this
        # fire without going back to the transcript.
        assert e["flagged_sentence"] == "Sorry, that table was wrong."
        assert "The suite is green" not in e["flagged_sentence"]

    def test_a_compliance_records_what_it_accepted(self, tmp_path):
        """A compliance is a label too, so it needs the same evidence."""
        transcript = self._make_transcript(tmp_path, "You're right, I missed it.")
        self._run(
            {"tool_name": "mcp__mgcp__add_lesson", "tool_input": {"id": "x"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        e = self._audit(tmp_path)[-1]
        assert e["event"] == "comply"
        assert e["pattern"] == r"\byou'?re right\b"
        assert e["flagged_sentence"] == "You're right, I missed it."

    def test_the_recorded_sentence_is_bounded(self, tmp_path):
        """The audit log is append-only, so one record cannot be unbounded."""
        transcript = self._make_transcript(tmp_path, "sorry " + "x" * 5000)
        self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        e = self._audit(tmp_path)[-1]
        assert len(e["flagged_sentence"]) <= 300

    def test_quoting_an_apology_is_recorded_as_such(self, tmp_path):
        """A known false-positive class, now visible instead of invisible.

        The gate matches on word boundaries, so discussing an apology reads as
        making one. This test does not assert the gate should allow it: the
        patterns are deliberately simple, and a cleverer matcher was built and
        removed before because it broke the canonical trigger. It asserts the
        record says what happened, so the rate can be counted.
        """
        transcript = self._make_transcript(
            tmp_path, 'The user replied "my bad" and I logged it.')
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": []}, tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        e = self._audit(tmp_path)[-1]
        assert e["pattern"] == r"\bmy bad\b"
        assert "my bad" in e["flagged_sentence"]

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

    def test_string_per_turn_values_do_not_open_the_gate(self, tmp_path):
        """workflow_state.json is agent-writable. With a STRING
        turn_tools_called, `name in called` is a substring test that any text
        naming the tool satisfies; set("*") from a string turn_bypass_scopes
        is bypass-all. Both must read as "no state", not "no enforcement"."""
        state_file = tmp_path / "state.json"
        rules_file = tmp_path / "enforcement_rules.json"
        rules_file.write_text(json.dumps({"version": 1, "rules": [GIT_GATE_RULE]}))
        for raw in ('{"turn_tools_called": "mcp__mgcp__query_lessons"}',
                    '{"turn_tools_called": "ran mcp__mgcp__query_lessons already"}',
                    '{"turn_bypass_scopes": "*"}',
                    '{"turn_bypass_scopes": "git"}'):
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


class TestTriggerMatches:
    """Trigger semantics, ported off the deleted enforcement.py copy.

    These ran against a module with no production caller. The hook's
    _trigger_matches is the one that decides real tool calls.
    """

    def test_wildcard_tool_matches_any(self, hook_module):
        trig = {"tool_name": "*"}
        assert hook_module._trigger_matches(trig, "Edit", {}) is True
        assert hook_module._trigger_matches(trig, "Bash", {"command": "ls"}) is True

    def test_exact_tool_matches(self, hook_module):
        trig = {"tool_name": "Edit"}
        assert hook_module._trigger_matches(trig, "Edit", {}) is True
        assert hook_module._trigger_matches(trig, "Write", {}) is False

    def test_command_match_requires_bash(self, hook_module):
        # command_match on a non-Bash tool is a no-match: there is no command.
        trig = {"tool_name": "*", "command_match": {"type": "contains", "pattern": "git"}}
        assert hook_module._trigger_matches(trig, "Edit", {"command": "git"}) is False

    def test_git_subcommand(self, hook_module):
        trig = {
            "tool_name": "Bash",
            "command_match": {"type": "git_subcommand", "subcommands": ["push"]},
        }
        assert hook_module._trigger_matches(trig, "Bash", {"command": "git push"}) is True
        assert hook_module._trigger_matches(trig, "Bash", {"command": "git commit"}) is False

    def test_empty_command_match_is_not_a_wildcard(self, hook_module):
        """`{}` is a matcher with no type, which the schema rejects. Reading it
        as "no matcher" made the rule fire on every call to the tool."""
        trig = {"tool_name": "Bash", "command_match": {}}
        assert hook_module._trigger_matches(trig, "Bash", {"command": "ls"}) is False

    def test_regex(self, hook_module):
        trig = {"tool_name": "Bash", "command_match": {"type": "regex", "pattern": r"rm\s+-rf"}}
        assert hook_module._trigger_matches(trig, "Bash", {"command": "rm -rf /"}) is True
        assert hook_module._trigger_matches(trig, "Bash", {"command": "ls"}) is False

    def test_contains(self, hook_module):
        trig = {"tool_name": "Bash", "command_match": {"type": "contains", "pattern": "sudo"}}
        assert hook_module._trigger_matches(trig, "Bash", {"command": "sudo apt"}) is True
        assert hook_module._trigger_matches(trig, "Bash", {"command": "apt"}) is False


class TestPreconditionTypes:
    """Every precondition type CLAUDE.md documents, against the live hook."""

    def test_tool_called_this_turn(self, hook_module):
        pre = {"type": "tool_called_this_turn", "tool_name": "foo"}
        ok, _ = hook_module._evaluate_precondition(pre, {"turn_tools_called": ["foo"]}, [])
        assert ok is True
        ok, _ = hook_module._evaluate_precondition(pre, {"turn_tools_called": []}, [])
        assert ok is False

    def test_tool_not_called_this_turn(self, hook_module):
        pre = {"type": "tool_not_called_this_turn", "tool_name": "bad"}
        ok, _ = hook_module._evaluate_precondition(pre, {"turn_tools_called": ["good"]}, [])
        assert ok is True
        ok, _ = hook_module._evaluate_precondition(pre, {"turn_tools_called": ["bad"]}, [])
        assert ok is False

    @staticmethod
    def _coupling_pre():
        return {
            "type": "staged_files_coupling",
            "couplings": [{"when_staged": ["src/*.py"], "require_one_of": ["README.md"]}],
        }

    def test_staged_files_coupling_satisfied(self, hook_module):
        ok, _ = hook_module._evaluate_precondition(
            self._coupling_pre(), {}, ["src/foo.py", "README.md"]
        )
        assert ok is True

    def test_staged_files_coupling_violated_names_the_trigger(self, hook_module):
        ok, detail = hook_module._evaluate_precondition(self._coupling_pre(), {}, ["src/foo.py"])
        assert ok is False
        assert "src/foo.py" in detail

    def test_staged_files_coupling_does_not_apply_when_unmatched(self, hook_module):
        ok, _ = hook_module._evaluate_precondition(self._coupling_pre(), {}, ["docs/foo.md"])
        assert ok is True

    def test_tool_input_glob_denies_on_match(self, hook_module):
        pre = {"type": "tool_input_glob", "field": "file_path", "deny_globs": ["**/settings.json"]}
        ok, _ = hook_module._evaluate_precondition(
            pre, {}, [], {"file_path": "/x/.claude/settings.json"}
        )
        assert ok is False

    def test_tool_input_glob_fails_open_on_missing_or_nonstring_field(self, hook_module):
        pre = {"type": "tool_input_glob", "field": "file_path", "deny_globs": ["**/*.json"]}
        ok, _ = hook_module._evaluate_precondition(pre, {}, [], {})
        assert ok is True
        ok, _ = hook_module._evaluate_precondition(pre, {}, [], {"file_path": 17})
        assert ok is True


class TestCheckCoupling:
    def test_empty_staged(self, hook_module):
        ok, trig = hook_module._check_coupling([], ["a"], ["b"])
        assert ok is True and trig == []

    def test_when_hit_req_hit(self, hook_module):
        ok, trig = hook_module._check_coupling(
            ["src/x.py", "README.md"], ["src/*.py"], ["README.md"]
        )
        assert ok is True and "src/x.py" in trig

    def test_when_hit_req_miss(self, hook_module):
        ok, trig = hook_module._check_coupling(["src/x.py"], ["src/*.py"], ["README.md"])
        assert ok is False and "src/x.py" in trig


class TestCommitMessageProseStyle:
    """The em dash check that runs on every commit.

    The style rule lived only in a lesson written on 2026-08-26. It did not hold,
    and the same complaint arrived on 2026-10-02 about this repository. The lesson
    was also unreachable at the moment it was needed, because
    query_lessons("git commit") did not return it. So the rule is now data the
    hook reads, and the check is one character with no guessing.

    These run the hook as a subprocess, which is how it runs in production.
    """

    RULE = {
        "name": "commit-message-prose-style",
        "description": "",
        "enabled": True,
        "trigger": {
            "tool_name": "Bash",
            "command_match": {
                "type": "git_subcommand",
                "subcommands": ["commit"],
                "pattern": "",
            },
        },
        "preconditions": [
            {
                "type": "tool_input_glob",
                "field": "command",
                "deny_globs": ["*\u2014*"],
                "couplings": [],
            },
        ],
        "bypass_scope": "prose",
        "deny_reason": "This commit message contains an em dash.",
    }

    def _run(self, command: str, tmp_path: Path, bypass: list | None = None):
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {"turn_tools_called": [], "turn_bypass_scopes": bypass or []}
            )
        )
        rules_file = tmp_path / "enforcement_rules.json"
        rules_file.write_text(json.dumps({"version": 1, "rules": [self.RULE]}))
        env = {
            "MGCP_STATE_FILE": str(state_file),
            "MGCP_ENFORCEMENT_CONFIG": str(rules_file),
            "MGCP_DATA_DIR": str(tmp_path),
            "PATH": "/usr/bin:/bin",
        }
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_em_dash_in_a_heredoc_message_is_refused(self, tmp_path):
        """A heredoc message still arrives inside the command string."""
        command = "git commit -F - <<'MSG'\nFix the thing \u2014 and another thing\nMSG"
        r = self._run(command, tmp_path)
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "em dash" in payload["hookSpecificOutput"]["permissionDecisionReason"]

    def test_em_dash_in_a_dash_m_message_is_refused(self, tmp_path):
        r = self._run('git commit -m "Fix the thing \u2014 and more"', tmp_path)
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_a_plain_message_passes(self, tmp_path):
        command = "git commit -F - <<'MSG'\nFix the thing. Then fix another thing.\nMSG"
        r = self._run(command, tmp_path)
        assert r.stdout.strip() == "", r.stdout

    def test_an_em_dash_outside_a_commit_is_ignored(self, tmp_path):
        """The rule reads commit messages, not every command on the machine."""
        r = self._run('echo "a \u2014 b" > /tmp/notes.txt', tmp_path)
        assert r.stdout.strip() == "", r.stdout

    def test_the_bypass_scope_releases_it(self, tmp_path):
        """A commit that quotes an em dash on purpose needs a way through."""
        r = self._run(
            'git commit -m "Ban the em dash \u2014 like this one"',
            tmp_path,
            bypass=["prose"],
        )
        assert r.stdout.strip() == "", r.stdout


class TestApologyPaymentIsReadFromTheTranscript:
    """A paid-for apology must not re-arm the gate on a mid-turn message.

    Diagnosed from a live transcript. UserPromptSubmit fires for a message the
    user sends while a turn is still running, and it clears
    `turn_tools_called`, which is the record that `add_lesson` already
    satisfied the gate. That message is written to the transcript as
    `queue-operation` and `attachment` entries and never as `type == "user"`,
    so the backwards walk sailed past it and kept reading the previous turn.
    State said "new turn, nothing called yet" while the transcript said "same
    turn, apology still present", so the gate denied again for an apology that
    had already been answered with a lesson. The sentence behind two real
    denials was "You're right, I narrated it instead of doing it", written and
    answered in the preceding turn.

    The first fix bounded the walk by `turn_started_at` from the same state
    file. An adversarial audit then showed that bound was itself a bypass: any
    value newer than this session's text stopped the walk at once, so the gate
    saw nothing and allowed the call with no audit row, and the value came from
    a shared agent-writable file. It also only fixed the false deny, leaving
    the mirror case where an UNANSWERED apology before the mid-turn message
    escaped.

    The gate now reads payment from the transcript: an `add_lesson` call later
    than the apology answers it, and nothing else does. The transcript is not
    shared between sessions and the gated agent cannot write it, so both
    directions are correct and neither depends on state.
    """

    _run = TestEnforcement._run

    @staticmethod
    def _two_turn_transcript(tmp_path, old_text, new_text, boundary):
        """A resolved apology before the boundary, plain text after it.

        The mid-turn message is written the way the harness really writes one:
        as queue-operation and attachment entries, with no user entry.
        """
        from datetime import UTC, datetime

        def stamp(offset):
            return datetime.fromtimestamp(boundary + offset, UTC).isoformat()

        def assistant(text, offset):
            return {"type": "assistant", "timestamp": stamp(offset),
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": text}]}}

        path = tmp_path / "transcript.jsonl"
        lines = [
            {"type": "user", "timestamp": stamp(-120),
             "message": {"role": "user", "content": "do the thing"}},
            assistant(old_text, -100),
            # The apology was answered here, in the turn that made it.
            {"type": "assistant", "timestamp": stamp(-90),
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "name": "mcp__mgcp__add_lesson", "input": {}}]}},
            # The mid-turn message: no user entry is ever written for it.
            {"type": "queue-operation", "timestamp": stamp(0)},
            {"type": "attachment", "timestamp": stamp(0)},
            assistant(new_text, 10),
        ]
        path.write_text("\n".join(json.dumps(x) for x in lines))
        return path

    def test_an_answered_apology_does_not_re_arm_after_a_mid_turn_message(
        self, tmp_path
    ):
        import time

        boundary = time.time() - 60
        transcript = self._two_turn_transcript(
            tmp_path,
            "You're right, I narrated it instead of doing it.",
            "Checking the suite now.",
            boundary,
        )
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript)},
            # Exactly the state UserPromptSubmit leaves behind: the tool list
            # is cleared, and turn_started_at marks when it cleared it.
            {"turn_tools_called": [], "turn_bypass_scopes": [],
             "turn_started_at": boundary},
            tmp_path, rules=[])
        assert r.stdout.strip() == "", (
            "the gate re-fired on an apology from before this turn: " + r.stdout
        )

    def test_an_apology_inside_this_turn_still_arms_the_gate(self, tmp_path):
        """The bound must not become a way to escape the gate."""
        import time

        boundary = time.time() - 60
        transcript = self._two_turn_transcript(
            tmp_path,
            "Everything was fine before.",
            "Sorry, that number was wrong.",
            boundary,
        )
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(transcript)},
            {"turn_tools_called": [], "turn_bypass_scopes": [],
             "turn_started_at": boundary},
            tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
        e = [json.loads(x) for x in
             (tmp_path / "gate_audit.jsonl").read_text().splitlines()][-1]
        assert e["flagged_sentence"] == "Sorry, that number was wrong."

    def test_a_transcript_with_no_timestamps_still_arms_the_gate(self, tmp_path):
        """Nothing in the decision reads a timestamp any more.

        The walk is ordered by position in the file, not by clock, so a
        transcript format that carries no timestamps is not a special case.
        """
        import time

        path = tmp_path / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in [
            {"type": "user", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Sorry, that was wrong."}]}},
        ]))
        r = self._run(
            {"tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "transcript_path": str(path)},
            {"turn_tools_called": [], "turn_bypass_scopes": [],
             "turn_started_at": time.time()},
            tmp_path, rules=[])
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestEveryHookSurvivesAMalformedSharedState:
    """workflow_state.json is agent-writable and shared by all five hooks.

    pre-tool-dispatcher has guarded against a valid-JSON non-object since
    v2.4, with a comment calling the isinstance check load-bearing. The other
    two hooks that read the same file did not, and neither has a top-level
    handler, so a state file holding `[]` crashed both on every message and
    every tool call: a TypeError on the key assignment in
    user-prompt-dispatcher and an AttributeError on .get() in
    post-tool-dispatcher. Found by an adversarial audit, 2026-10-03.

    The failure was not only lost advisory text. PreToolUse survived and kept
    denying git without query_lessons, while the UserPromptSubmit hook that
    delivers the routing block and parses MGCP_BYPASS was dead, so the bypass
    token could not be read and the gate could not be opened.
    """

    TEMPLATES = HOOK_PATH.parent

    HOOKS = [
        ("user-prompt-dispatcher.py", {"prompt": "please commit this"}),
        ("post-tool-dispatcher.py", {"tool_name": "Bash", "tool_response": "ok"}),
        ("pre-tool-dispatcher.py",
         {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}),
        ("session-init.py", {"session_id": "s1"}),
        ("mgcp-precompact.py", {"session_id": "s1"}),
    ]

    @pytest.mark.parametrize("hook_name,payload", HOOKS)
    @pytest.mark.parametrize("shape", ["[]", '"a string"', "42", "null", "true", "{}"])
    def test_a_non_object_state_file_does_not_crash_the_hook(
        self, hook_name, payload, shape, tmp_path
    ):
        state_file = tmp_path / "workflow_state.json"
        state_file.write_text(shape)
        result = subprocess.run(
            [sys.executable, str(self.TEMPLATES / hook_name)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={
                "MGCP_STATE_FILE": str(state_file),
                "MGCP_DATA_DIR": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        )
        assert result.returncode == 0, (
            f"{hook_name} crashed on state={shape}: {result.stderr[-400:]}"
        )
        assert "Traceback" not in result.stderr, result.stderr[-400:]

    def test_the_git_gate_still_denies_when_state_is_malformed(self, tmp_path):
        """Degrading to "no state" must not degrade to "no enforcement"."""
        state_file = tmp_path / "workflow_state.json"
        state_file.write_text("[]")
        rules_file = tmp_path / "enforcement_rules.json"
        rules_file.write_text(json.dumps({"version": 1, "rules": [GIT_GATE_RULE]}))
        result = subprocess.run(
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
        assert result.returncode == 0
        decision = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny"
