"""Contract tests for the adjudicate_apology_gate MCP tool (v2.11).

The tool is the gate's second exit: contest on the record. These tests call
the tool function directly against a sandboxed MGCP_DATA_DIR/MGCP_STATE_FILE
and assert the two artifacts it must produce — the append-only audit line and
the per-turn adjudication state the PreToolUse hook reads.
"""

import asyncio
import json

import pytest

from mgcp.server import adjudicate_apology_gate


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MGCP_STATE_FILE", str(tmp_path / "workflow_state.json"))
    return tmp_path


def _call(**kwargs):
    return asyncio.run(adjudicate_apology_gate(**kwargs))


def _audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / "gate_audit.jsonl").read_text().splitlines()]


def test_not_apology_verdict_records_and_opens(sandbox):
    result = _call(
        flagged_sentence="The user said sorry in the transcript we parsed.",
        verdict="not_apology",
        reasoning="Quoted speech: the transcript's author apologized, not me.",
    )
    assert "false positive" in result.lower()

    events = _audit(sandbox)
    assert events[-1]["event"] == "adjudication"
    assert events[-1]["verdict"] == "not_apology"
    assert "Quoted speech" in events[-1]["reasoning"]

    state = json.loads((sandbox / "workflow_state.json").read_text())
    assert state["turn_apology_adjudication"]["verdict"] == "not_apology"


def test_apology_verdict_records_but_directs_to_add_lesson(sandbox):
    result = _call(
        flagged_sentence="sorry, I broke the build",
        verdict="apology",
        reasoning="Genuine: I acknowledged my own broken commit in this turn.",
    )
    assert "add_lesson" in result

    state = json.loads((sandbox / "workflow_state.json").read_text())
    # the hook only opens the gate for verdict == "not_apology"
    assert state["turn_apology_adjudication"]["verdict"] == "apology"


def test_short_reasoning_is_refused_and_nothing_is_written(sandbox):
    result = _call(flagged_sentence="s", verdict="not_apology", reasoning="nah")
    assert "too short" in result
    assert not (sandbox / "gate_audit.jsonl").exists()
    assert not (sandbox / "workflow_state.json").exists()


def test_unknown_verdict_is_refused(sandbox):
    result = _call(
        flagged_sentence="s", verdict="maybe",
        reasoning="a perfectly long reasoning string over twenty chars",
    )
    assert "verdict must be" in result
    assert not (sandbox / "gate_audit.jsonl").exists()


def test_existing_state_is_merged_not_clobbered(sandbox):
    (sandbox / "workflow_state.json").write_text(
        json.dumps({"turn_tools_called": ["Bash"], "current_call_count": 7})
    )
    _call(
        flagged_sentence="s", verdict="not_apology",
        reasoning="bystander sentence about the gate, not an apology by me",
    )
    state = json.loads((sandbox / "workflow_state.json").read_text())
    assert state["turn_tools_called"] == ["Bash"]
    assert state["current_call_count"] == 7
    assert state["turn_apology_adjudication"]["verdict"] == "not_apology"
