"""Checks on the clock injected into every turn.

A model reads a transcript with no sense of elapsed time. A reply written three
hours later reads exactly like one written in ten seconds, so "we just did X"
stops being true with nothing to say so. This hook puts the current time, the
gap since the previous message, and the session length at the top of every turn.

The clock must never be able to take the injected block down with it, so the
failure cases are tested as carefully as the arithmetic.
"""

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "mgcp" / "hook_templates" / "user-prompt-dispatcher.py"
)


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("user_prompt_dispatcher", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestShortDelta:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "just now"),
            (59, "just now"),
            (60, "1m"),
            (359, "5m"),
            (3599, "59m"),
            (3600, "1h"),
            (86399, "23h"),
            (86400, "1d"),
            (172800, "2d"),
        ],
    )
    def test_the_ladder(self, hook, seconds, expected):
        assert hook._short_delta(seconds) == expected

    def test_a_negative_gap_does_not_render(self, hook):
        """A clock change must not produce "-4m since your last message"."""
        assert hook._short_delta(-500) == "just now"

    def test_minutes_matter_here_and_not_in_the_package_helper(self, hook):
        """The two helpers differ below an hour on purpose.

        `relative_age` calls anything under an hour "now", which is right for the
        age of a note. A six minute gap between messages is worth saying.
        """
        from datetime import UTC, datetime, timedelta

        from mgcp.models import relative_age

        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        assert relative_age(now - timedelta(minutes=6), now) == "now"
        assert hook._short_delta(6 * 60) == "6m"

    @pytest.mark.parametrize("hours", [1, 2, 5, 23])
    def test_the_two_helpers_agree_from_one_hour_up(self, hook, hours):
        """Two formatters are two places for one bug, so the overlap is pinned."""
        from datetime import UTC, datetime, timedelta

        from mgcp.models import relative_age

        now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
        assert hook._short_delta(hours * 3600) == relative_age(
            now - timedelta(hours=hours), now
        )


class TestClockLine:
    NOW = 1_767_000_000.0  # a fixed instant, so the test does not depend on today

    def test_the_first_message_says_so(self, hook):
        line = hook._clock_line({}, "session-a", self.NOW)
        assert line.startswith("<time>⌚ ")
        assert "first message of this session" in line
        assert "since your last message" not in line

    def test_a_later_message_carries_the_gap(self, hook):
        state = {"turn_session_id": "session-a", "turn_started_at": self.NOW - 360}
        line = hook._clock_line(state, "session-a", self.NOW)
        assert "6m since your last message" in line

    def test_it_reports_the_session_length(self, hook):
        state = {
            "turn_session_id": "session-a",
            "turn_started_at": self.NOW - 60,
            "session_started_at": self.NOW - 48 * 60,
        }
        assert "48m into this session" in hook._clock_line(state, "session-a", self.NOW)

    def test_a_session_under_a_minute_old_omits_its_length(self, hook):
        """One more clause that says nothing is one more clause on every turn."""
        state = {
            "turn_session_id": "session-a",
            "turn_started_at": self.NOW - 70,
            "session_started_at": self.NOW - 10,
        }
        assert "into this session" not in hook._clock_line(state, "session-a", self.NOW)

    def test_a_new_session_id_discards_the_previous_timings(self, hook):
        """State survives a restart, and the previous session's times are wrong."""
        state = {"turn_session_id": "old", "turn_started_at": self.NOW - 99999}
        line = hook._clock_line(state, "new", self.NOW)
        assert "first message of this session" in line
        assert "since your last message" not in line

    def test_junk_in_the_state_still_yields_a_clock(self, hook):
        for junk in ({"turn_started_at": "yesterday"}, {"turn_started_at": None}, {}):
            line = hook._clock_line(junk, "s", self.NOW)
            assert line.startswith("<time>⌚ ")

    def test_it_stays_short(self, hook):
        """It is on every turn, so the whole line is budgeted."""
        state = {
            "turn_session_id": "s",
            "turn_started_at": self.NOW - 3600 * 5,
            "session_started_at": self.NOW - 3600 * 9,
        }
        assert len(hook._clock_line(state, "s", self.NOW)) <= 100


class TestItReachesTheOutput:
    """End to end, as a subprocess, which is how the hook runs."""

    def _run(self, tmp_path, prompt, session_id, state=None):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(state or {}))
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps({"prompt": prompt, "session_id": session_id}),
            capture_output=True,
            text=True,
            env={
                "MGCP_STATE_FILE": str(state_file),
                "MGCP_DATA_DIR": str(tmp_path),
                "PATH": "/usr/bin:/bin",
            },
        )
        return result, state_file

    def test_the_clock_is_the_first_thing_printed(self, tmp_path):
        result, _ = self._run(tmp_path, "hello", "s1")
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[0].startswith("<time>⌚ ")

    def test_the_turn_time_is_recorded_for_the_next_turn(self, tmp_path):
        result, state_file = self._run(tmp_path, "hello", "s1")
        assert result.returncode == 0, result.stderr
        state = json.loads(state_file.read_text())
        assert abs(state["turn_started_at"] - time.time()) < 60
        assert abs(state["session_started_at"] - time.time()) < 60
        assert state["turn_session_id"] == "s1"

    def test_the_second_turn_shows_a_gap(self, tmp_path):
        now = time.time()
        result, _ = self._run(
            tmp_path,
            "again",
            "s1",
            state={"turn_session_id": "s1", "turn_started_at": now - 420},
        )
        assert "7m since your last message" in result.stdout.splitlines()[0]
