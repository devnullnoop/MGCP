"""Integration tests for MGCP v2.0 intent-based routing.

Tests the rewritten hooks and new MCP tools:
- session-init.py output format (routing prompt + intent-action map)
- user-prompt-dispatcher.py simplification (no regex, state-based only)
- update_workflow_state MCP tool
- intent_calibration REM operation
- Token budget verification
- State file backwards compatibility
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Hook paths — templates live in the package, not in .claude/hooks/
HOOKS_DIR = Path(__file__).parent.parent / "src" / "mgcp" / "hook_templates"
SESSION_INIT = HOOKS_DIR / "session-init.py"
DISPATCHER = HOOKS_DIR / "user-prompt-dispatcher.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Use a dedicated temp state file for tests to avoid conflicts with
# the live Claude Code session's hooks writing to the real state file.
_test_state_dir = tempfile.mkdtemp(prefix="mgcp-test-")
TEST_STATE_FILE = Path(_test_state_dir) / "workflow_state.json"


def run_hook(hook_path: Path, stdin_data: str = "") -> str:
    """Run a hook script and capture its stdout."""
    env = {**__import__("os").environ, "CLAUDE_PROJECT_DIR": "/tmp/test-project"}
    # Point dispatcher at the test state file instead of the live one
    env["MGCP_STATE_FILE"] = str(TEST_STATE_FILE)
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result.stdout


def write_state(state: dict) -> None:
    """Write a workflow state file for testing."""
    TEST_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TEST_STATE_FILE, "w") as f:
        json.dump(state, f)


def read_state() -> dict:
    """Read the current test workflow state file."""
    if TEST_STATE_FILE.exists():
        with open(TEST_STATE_FILE) as f:
            return json.load(f)
    return {}


def backup_and_restore_state():
    """Context manager to backup and restore the state file."""

    class StateBackup:
        def __init__(self):
            self.original = None

        def __enter__(self):
            if TEST_STATE_FILE.exists():
                with open(TEST_STATE_FILE) as f:
                    self.original = f.read()
            return self

        def __exit__(self, *args):
            if self.original is not None:
                with open(TEST_STATE_FILE, "w") as f:
                    f.write(self.original)
            elif TEST_STATE_FILE.exists():
                TEST_STATE_FILE.unlink()

    return StateBackup()


# ---------------------------------------------------------------------------
# Session Init Tests
# ---------------------------------------------------------------------------

class TestSessionInitOutput:
    """Tests for the rewritten session-init.py hook."""

    def test_output_is_valid_json(self):
        """Hook outputs valid JSON with hookSpecificOutput structure."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        assert "hookSpecificOutput" in data
        assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "additionalContext" in data["hookSpecificOutput"]

    def test_no_intent_routing_tags(self):
        """v2.5: SessionStart no longer injects routing/actions blocks.

        The UserPromptSubmit dispatcher re-injects the full (classifier +
        inline actions) block on every message, so a SessionStart copy is
        pure duplication. See v2.5 CHANGELOG.
        """
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert "<intent-routing>" not in context
        assert "<intent-actions>" not in context

    def test_contains_session_start_checklist(self):
        """Output contains the three bootstrap calls: soliloquy, context, lessons."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert "read_soliloquy" in context
        assert "get_project_context" in context
        assert "query_lessons" in context

    def test_token_budget(self):
        """Total injection should be < 1000 tokens (rough estimate: chars/4)."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(context) / 4
        assert estimated_tokens < 1000, (
            f"Session init injects ~{estimated_tokens:.0f} tokens, "
            f"should be < 1000 (was ~2000 in v1.2)"
        )

    def test_contains_update_workflow_state(self):
        """Output references the new update_workflow_state tool."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert "update_workflow_state" in context

    def test_contains_project_path(self):
        """Output contains the project path from environment."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert "/tmp/test-project" in context


# ---------------------------------------------------------------------------
# Dispatcher Tests
# ---------------------------------------------------------------------------

class TestDispatcherSimplification:
    """Tests for the simplified user-prompt-dispatcher.py."""

    def test_no_regex_output_for_task_start(self):
        """'fix the bug' should produce NO intent-specific output (regex removed).

        The intent-routing block is always injected (survives context compaction),
        so we strip it before checking for regex-triggered content.
        """
        with backup_and_restore_state():
            write_state({"current_call_count": 0})
            hook_input = json.dumps({"prompt": "fix the bug"})
            output = run_hook(DISPATCHER, hook_input)
            # Strip the always-present intent-routing block
            import re as re_mod
            stripped = re_mod.sub(r"<intent-routing>.*?</intent-routing>", "", output, flags=re_mod.DOTALL).strip()
            # No regex means no pattern matching output beyond intent-routing
            assert "STOP" not in stripped
            assert "<workflow-state>" not in stripped

    def test_neutral_message_zero_output(self):
        """'ok' with no state produces only the two unconditional blocks.

        Two blocks print on every message by design: the intent router, and the
        clock added in hook version 2.14. Both are stripped here, because the
        property this guards is that a neutral message triggers no gate, no
        reminder, and no workflow text.
        """
        with backup_and_restore_state():
            write_state({
                "current_call_count": 0,
                "remind_at_call": 0,
                "remind_at_time": 0,
                "active_workflow": None,
            })
            hook_input = json.dumps({"prompt": "ok"})
            output = run_hook(DISPATCHER, hook_input)
            import re as re_mod
            stripped = re_mod.sub(
                r"<intent-routing>.*?</intent-routing>", "", output, flags=re_mod.DOTALL
            )
            stripped = re_mod.sub(r"<time>.*?</time>", "", stripped, flags=re_mod.DOTALL).strip()
            assert stripped == ""

    def test_workflow_state_injection(self):
        """Active workflow in state file produces <workflow-state> output."""
        with backup_and_restore_state():
            write_state({
                "current_call_count": 5,
                "remind_at_call": 0,
                "remind_at_time": 0,
                "active_workflow": "feature-development",
                "current_step": "execute",
                "workflow_complete": False,
                "steps_completed": ["research", "plan"],
            })
            hook_input = json.dumps({"prompt": "continue"})
            output = run_hook(DISPATCHER, hook_input)
            assert "<workflow-state>" in output
            assert "feature-development" in output
            assert "execute" in output

    def test_scheduled_reminder_fires(self):
        """Pending reminder fires and is consumed."""
        with backup_and_restore_state():
            write_state({
                "current_call_count": 4,
                "remind_at_call": 5,
                "remind_at_time": 0,
                "reminder_message": "EXECUTE Plan step NOW",
                "lesson_ids": [],
                "workflow_step": "feature-development/plan",
                "active_workflow": None,
            })
            hook_input = json.dumps({"prompt": "sounds good"})
            output = run_hook(DISPATCHER, hook_input)
            assert "<scheduled-reminder>" in output
            assert "EXECUTE Plan step NOW" in output
            assert "feature-development" in output

            # Verify reminder was consumed
            state = read_state()
            assert state["remind_at_call"] == 0
            assert state["reminder_message"] == ""

    def test_counter_increments(self):
        """Call counter increments on each invocation."""
        with backup_and_restore_state():
            write_state({"current_call_count": 10})
            hook_input = json.dumps({"prompt": "test"})
            run_hook(DISPATCHER, hook_input)
            state = read_state()
            assert state["current_call_count"] == 11


# ---------------------------------------------------------------------------
# State Compatibility Tests
# ---------------------------------------------------------------------------

class TestStateCompatibility:
    """Test that v2.0 dispatcher reads v1.2 state file format."""

    def test_v12_state_file_loads(self):
        """V1.2 state file (without workflow fields) loads with defaults."""
        with backup_and_restore_state():
            # V1.2 state only had reminder fields, no workflow fields
            v12_state = {
                "current_call_count": 42,
                "remind_at_call": 0,
                "remind_at_time": 0,
                "reminder_message": "",
                "lesson_ids": [],
                "workflow_step": "",
                "task_note": "",
            }
            write_state(v12_state)
            hook_input = json.dumps({"prompt": "hello"})
            output = run_hook(DISPATCHER, hook_input)
            # Should not crash, and should not inject workflow state
            assert "<workflow-state>" not in output

    def test_v12_state_with_reminders(self):
        """V1.2 state with active reminder still fires correctly."""
        with backup_and_restore_state():
            v12_state = {
                "current_call_count": 9,
                "remind_at_call": 10,
                "remind_at_time": 0,
                "reminder_message": "Check results",
                "lesson_ids": ["verify-api-response"],
                "workflow_step": "",
                "task_note": "Testing",
            }
            write_state(v12_state)
            hook_input = json.dumps({"prompt": "ok"})
            output = run_hook(DISPATCHER, hook_input)
            assert "<scheduled-reminder>" in output
            assert "Check results" in output


# ---------------------------------------------------------------------------
# update_workflow_state Tool Tests
# ---------------------------------------------------------------------------

class TestUpdateWorkflowState:
    """Tests for the reminder_state.update_workflow_state function."""

    def test_activate_workflow(self):
        """Setting active_workflow updates state correctly."""
        from mgcp.reminder_state import save_state, update_workflow_state

        with backup_and_restore_state():
            save_state({"current_call_count": 0})
            result = update_workflow_state(active_workflow="bug-fix")
            assert result["active_workflow"] == "bug-fix"
            assert result["steps_completed"] == []

    def test_step_completion(self):
        """Marking steps as completed accumulates in the list."""
        from mgcp.reminder_state import save_state, update_workflow_state

        with backup_and_restore_state():
            save_state({"current_call_count": 0, "active_workflow": "feature-development"})
            update_workflow_state(step_completed="research")
            result = update_workflow_state(step_completed="plan")
            assert "research" in result["steps_completed"]
            assert "plan" in result["steps_completed"]

    def test_reactivating_a_finished_workflow_clears_the_complete_flag(self):
        """Starting a run means it is not complete -- even the same workflow.

        The reset was scoped to the different-workflow branch, so re-running a
        finished workflow reported COMPLETE from its first step. The hook reads
        this flag to decide whether to keep injecting workflow context.
        """
        from mgcp.reminder_state import save_state, update_workflow_state

        with backup_and_restore_state():
            save_state({
                "current_call_count": 0,
                "active_workflow": "feature-development",
                "workflow_complete": True,
                "steps_completed": ["research", "plan"],
            })
            result = update_workflow_state(active_workflow="feature-development")
            assert result["workflow_complete"] is False, (
                "re-activating a finished workflow still reported it complete"
            )

    def test_activating_still_honours_an_explicit_complete_in_the_same_call(self):
        """activate + complete in one call must end complete, not reset."""
        from mgcp.reminder_state import save_state, update_workflow_state

        with backup_and_restore_state():
            save_state({"current_call_count": 0})
            result = update_workflow_state(
                active_workflow="bug-fix", workflow_complete=True
            )
            assert result["workflow_complete"] is True

    def test_workflow_complete(self):
        """Marking workflow complete clears current_step."""
        from mgcp.reminder_state import save_state, update_workflow_state

        with backup_and_restore_state():
            save_state({
                "current_call_count": 0,
                "active_workflow": "feature-development",
                "current_step": "review",
            })
            result = update_workflow_state(workflow_complete=True)
            assert result["workflow_complete"] is True
            assert result["current_step"] is None


# ---------------------------------------------------------------------------
# Intent Calibration REM Tests
# ---------------------------------------------------------------------------

class TestIntentCalibration:
    """Tests for the REM intent_calibration operation."""

    def test_intent_calibration_in_default_schedules(self):
        """intent_calibration is in DEFAULT_SCHEDULES."""
        from mgcp.rem_config import DEFAULT_SCHEDULES

        assert "intent_calibration" in DEFAULT_SCHEDULES
        schedule = DEFAULT_SCHEDULES["intent_calibration"]
        assert schedule.strategy == "linear"
        assert schedule.interval == 10

    def test_rem_engine_routes_to_intent_calibration(self):
        """RemEngine._run_operation dispatches to _intent_calibration."""
        import asyncio

        from mgcp.persistence import LessonStore
        from mgcp.rem_cycle import RemEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = LessonStore(db_path=db_path)
            engine = RemEngine(store=store, project_id="test-project")

            async def run():
                findings = await engine._run_operation("intent_calibration", session_number=10)
                return findings

            findings = asyncio.run(run())
            assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Project-local hook configuration
# ---------------------------------------------------------------------------

class TestProjectSettingsStayEmpty:
    """This repository must not commit its own hook registrations.

    MGCP deploys hooks globally to ~/.mgcp/hooks and registers them in
    ~/.claude/settings.json. A project-local copy would fire a second time on
    every event, so the tracked file stays empty on purpose.

    A sibling test used to assert that three superseded hooks still existed in
    examples/claude-hooks/legacy/. Nothing imported, executed or compared
    against them, so the test protected the archive and the archive existed to
    satisfy the test. Both were deleted; git history holds the files.
    """

    def test_settings_json_empty(self):
        """Project settings.json should be empty (hooks are deployed globally)."""
        settings_path = Path(__file__).parent.parent / ".claude" / "settings.json"
        with open(settings_path) as f:
            settings = json.load(f)
        assert settings == {}


class TestBypassTokenParsing:
    """The MGCP_BYPASS parse is the hook's, and it had no test.

    Every other bypass test injects turn_bypass_scopes directly into the
    state file, so the regex that actually produces it — the one a user's
    typed token has to satisfy — was unguarded. The copy in enforcement.py
    that used to be tested here had no production caller.
    """

    @staticmethod
    def _scopes_for(prompt: str) -> list:
        state_file = Path(tempfile.mkdtemp(prefix="mgcp-bypass-")) / "workflow_state.json"
        env = {
            **__import__("os").environ,
            "CLAUDE_PROJECT_DIR": "/tmp/test-project",
            "MGCP_STATE_FILE": str(state_file),
        }
        subprocess.run(
            [sys.executable, str(DISPATCHER)],
            input=json.dumps({"prompt": prompt}),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return json.loads(state_file.read_text()).get("turn_bypass_scopes", [])

    def test_bare_token_disables_everything(self):
        assert self._scopes_for("hello MGCP_BYPASS world") == ["*"]

    def test_scoped_token(self):
        assert self._scopes_for("MGCP_BYPASS:git") == ["git"]

    def test_multiple_scopes_accumulate(self):
        out = self._scopes_for("MGCP_BYPASS:git and MGCP_BYPASS:docs")
        assert set(out) == {"git", "docs"}

    def test_scope_is_lowercased_like_the_token(self):
        # Every rule's bypass_scope is lowercase and the hook compares with
        # `in`, so a verbatim "GIT" is an opt-out that can never match.
        assert self._scopes_for("mgcp_bypass:Git") == ["git"]
        assert self._scopes_for("MGCP_BYPASS:GIT") == ["git"]

    def test_ordinary_prompt_yields_no_bypass(self):
        assert self._scopes_for("just a normal prompt") == []

    def test_word_is_not_enough(self):
        # "bypass" on its own must not open the gate.
        assert self._scopes_for("please bypass the check") == []


class TestTurnSessionIdIsRecorded:
    """The apology gate's contest exit needs this: the PreToolUse gate matches
    the adjudication's session_id exactly, and only this hook knows the value.
    """

    @staticmethod
    def _state_after(payload: dict) -> dict:
        state_file = Path(tempfile.mkdtemp(prefix="mgcp-sid-")) / "workflow_state.json"
        env = {
            **__import__("os").environ,
            "CLAUDE_PROJECT_DIR": "/tmp/test-project",
            "MGCP_STATE_FILE": str(state_file),
        }
        subprocess.run(
            [sys.executable, str(DISPATCHER)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return json.loads(state_file.read_text())

    def test_session_id_from_hook_input_is_persisted(self):
        state = self._state_after({"prompt": "hello", "session_id": "abc-123-harness"})
        assert state["turn_session_id"] == "abc-123-harness"

    def test_missing_session_id_records_empty_string(self):
        state = self._state_after({"prompt": "hello"})
        assert state["turn_session_id"] == ""

    def test_null_session_id_records_empty_string(self):
        # A None must not land as null and then compare equal to nothing.
        state = self._state_after({"prompt": "hello", "session_id": None})
        assert state["turn_session_id"] == ""
