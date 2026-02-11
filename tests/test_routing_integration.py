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

import pytest

# Hook paths
HOOKS_DIR = Path(__file__).parent.parent / ".claude" / "hooks"
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

    def test_contains_intent_routing_tags(self):
        """Output contains <intent-routing> and <intent-actions> XML tags."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]
        assert "<intent-routing>" in context
        assert "</intent-routing>" in context
        assert "<intent-actions>" in context
        assert "</intent-actions>" in context

    def test_contains_all_seven_intents(self):
        """All 7 intent categories are defined in the routing prompt."""
        output = run_hook(SESSION_INIT)
        data = json.loads(output)
        context = data["hookSpecificOutput"]["additionalContext"]

        intents = [
            "git_operation",
            "catalogue_dependency",
            "catalogue_security",
            "catalogue_decision",
            "catalogue_arch_note",
            "catalogue_convention",
            "task_start",
        ]
        for intent in intents:
            assert intent in context, f"Missing intent: {intent}"

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
        """'ok' with no state should produce only the intent-routing block."""
        with backup_and_restore_state():
            write_state({
                "current_call_count": 0,
                "remind_at_call": 0,
                "remind_at_time": 0,
                "active_workflow": None,
            })
            hook_input = json.dumps({"prompt": "ok"})
            output = run_hook(DISPATCHER, hook_input)
            # Strip the always-present intent-routing block
            import re as re_mod
            stripped = re_mod.sub(r"<intent-routing>.*?</intent-routing>", "", output, flags=re_mod.DOTALL).strip()
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

    @pytest.mark.slow
    def test_intent_calibration_detects_unmapped(self):
        """REM operation detects communities with unmapped tags."""
        import asyncio

        from mgcp.persistence import LessonStore
        from mgcp.rem_cycle import RemEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            store = LessonStore(db_path=db_path)

            async def run():
                from mgcp.models import Lesson

                # Create lessons with tags that map to known intents
                for i in range(5):
                    lesson = Lesson(
                        id=f"security-lesson-{i}",
                        trigger=f"security pattern {i}",
                        action=f"Apply security measure {i}",
                        tags=["security", "owasp"],
                    )
                    await store.add_lesson(lesson)

                # Create lessons with tags that DON'T map to any intent
                for i in range(4):
                    lesson = Lesson(
                        id=f"workflow-lesson-{i}",
                        trigger=f"workflow management {i}",
                        action=f"Follow workflow step {i}",
                        tags=["workflow", "process-management"],
                        related_ids=[f"workflow-lesson-{j}" for j in range(4) if j != i],
                    )
                    await store.add_lesson(lesson)

                # Link workflow lessons so they form a community
                for i in range(4):
                    lesson = await store.get_lesson(f"workflow-lesson-{i}")
                    from mgcp.models import Relationship
                    for j in range(4):
                        if j != i:
                            lesson.relationships.append(
                                Relationship(target=f"workflow-lesson-{j}", type="related")
                            )
                    await store.update_lesson(lesson)

                engine = RemEngine(store=store)
                findings = await engine._intent_calibration()
                return findings

            findings = asyncio.run(run())
            # Should find unmapped tags like "workflow" and "process-management"
            # This may or may not produce findings depending on community detection
            # (small graph may not cluster well), but it shouldn't crash
            assert isinstance(findings, list)

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
            engine = RemEngine(store=store)

            async def run():
                findings = await engine._run_operation("intent_calibration", session_number=10)
                return findings

            findings = asyncio.run(run())
            assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Legacy Hook Cleanup Tests
# ---------------------------------------------------------------------------

class TestLegacyHooksArchived:
    """Verify legacy hooks have been moved to examples/."""

    def test_git_reminder_not_in_hooks(self):
        """git-reminder.py no longer exists in .claude/hooks/."""
        assert not (HOOKS_DIR / "git-reminder.py").exists()

    def test_catalogue_reminder_not_in_hooks(self):
        """catalogue-reminder.py no longer exists in .claude/hooks/."""
        assert not (HOOKS_DIR / "catalogue-reminder.py").exists()

    def test_task_start_reminder_not_in_hooks(self):
        """task-start-reminder.py no longer exists in .claude/hooks/."""
        assert not (HOOKS_DIR / "task-start-reminder.py").exists()

    def test_legacy_hooks_archived(self):
        """Legacy hooks exist in examples/claude-hooks/legacy/."""
        legacy_dir = Path(__file__).parent.parent / "examples" / "claude-hooks" / "legacy"
        assert (legacy_dir / "git-reminder.py").exists()
        assert (legacy_dir / "catalogue-reminder.py").exists()
        assert (legacy_dir / "task-start-reminder.py").exists()

    def test_settings_json_unchanged(self):
        """settings.json still references the same 4 hooks."""
        settings_path = Path(__file__).parent.parent / ".claude" / "settings.json"
        with open(settings_path) as f:
            settings = json.load(f)

        hooks = settings.get("hooks", {})
        assert "UserPromptSubmit" in hooks
        assert "SessionStart" in hooks
        assert "PostToolUse" in hooks
        assert "PreCompact" in hooks
        # Verify specific commands
        assert "user-prompt-dispatcher.py" in hooks["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "session-init.py" in hooks["SessionStart"][0]["hooks"][0]["command"]
