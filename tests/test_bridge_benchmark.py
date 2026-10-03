"""Checks on the bridge measurement tools.

The measurement reads `query_lessons` output as text, so the parser is the part
that can quietly go wrong. A parser that silently finds no bridged lessons would
report that the bridge never fires, which is the same shape as a real finding.
"""

import json

from tests.bridge_benchmark import BRIDGE_MARKER, LESSON_ID, analyse

SAMPLE = """Found 3 relevant lessons:

**reduction-over-addition**: Default to REMOVING. Add only when there is no path.
  Why: Operator named additive work as the worst tendency.
  (relevance: 71%)

**lint-before-push**: Run ruff before pushing.
  (relevance: 66%)


**Also relevant** (via community: _Session Discipline_):

**query-before-git-operations**: Query lessons before any git operation.
  (relevance: 54%, via community)

**mgcp-save-before-commit**: Save project context first.
  (relevance: 51%, via community)
"""


class TestParser:
    def test_it_separates_searched_from_appended(self):
        head, marker, tail = SAMPLE.partition(BRIDGE_MARKER)
        assert marker == BRIDGE_MARKER
        assert LESSON_ID.findall(head) == ["reduction-over-addition", "lint-before-push"]
        assert LESSON_ID.findall(tail) == [
            "query-before-git-operations",
            "mgcp-save-before-commit",
        ]

    def test_output_with_no_appended_section(self):
        head, marker, tail = "**only-one**: text\n  (relevance: 60%)".partition(BRIDGE_MARKER)
        assert marker == ""
        assert LESSON_ID.findall(head) == ["only-one"]
        assert LESSON_ID.findall(tail) == []

    def test_the_marker_itself_is_not_read_as_a_lesson(self):
        """The heading is bold text followed by a space, not a lesson id."""
        assert LESSON_ID.findall(BRIDGE_MARKER + " (via community: _x_):") == []

    def test_it_does_not_match_bold_prose(self):
        assert LESSON_ID.findall("**Note**: this is prose, not an id") == []
        assert LESSON_ID.findall("**Has Capitals**: nope") == []

    def test_it_reads_ids_with_digits(self):
        assert LESSON_ID.findall("**rule-2-of-3**: text") == ["rule-2-of-3"]


class TestAnalysis:
    def _write(self, tmp_path, name, rows):
        path = tmp_path / name
        path.write_text(json.dumps({"condition": name, "rows": rows}))
        return path

    def test_a_useful_append_is_counted(self, tmp_path):
        rows_on = [
            {
                "id": "q1",
                "kind": "positive",
                "gold": "a",
                "relevant": ["a", "b"],
                "direct": ["a"],
                "bridged": ["b"],
            }
        ]
        rows_off = [{**rows_on[0], "bridged": []}]
        result = analyse(
            self._write(tmp_path, "on.json", rows_on),
            self._write(tmp_path, "off.json", rows_off),
        )
        assert result["appends_that_added_a_labelled_lesson"] == 1
        assert result["useful_detail"] == [{"query": "q1", "added": ["b"]}]
        assert result["direct_results_differ"] == []

    def test_an_append_already_in_the_results_is_not_useful(self, tmp_path):
        """Appending a lesson search already returned adds nothing."""
        rows_on = [
            {
                "id": "q1",
                "kind": "positive",
                "gold": "a",
                "relevant": ["a"],
                "direct": ["a"],
                "bridged": ["a"],
            }
        ]
        rows_off = [{**rows_on[0], "bridged": []}]
        result = analyse(
            self._write(tmp_path, "on.json", rows_on),
            self._write(tmp_path, "off.json", rows_off),
        )
        assert result["appends_that_added_a_labelled_lesson"] == 0

    def test_differing_searched_results_void_the_comparison(self, tmp_path):
        """Only the bridge should change. Anything else makes the run unreadable."""
        rows_on = [
            {"id": "q1", "kind": "positive", "gold": "a", "relevant": [], "direct": ["a"], "bridged": []}
        ]
        rows_off = [
            {"id": "q1", "kind": "positive", "gold": "a", "relevant": [], "direct": ["z"], "bridged": []}
        ]
        result = analyse(
            self._write(tmp_path, "on.json", rows_on),
            self._write(tmp_path, "off.json", rows_off),
        )
        assert result["direct_results_differ"] == ["q1"]


class TestShippedThresholdMatchesTheSweep:
    """The shipped floor must be the one the sweep chose, with its evidence.

    docs/bridge-results/bridge-sweep.json is the record. If someone changes
    BRIDGE_MIN_SCORE without re-running the sweep, these fail and say so.
    """

    def test_server_uses_the_measured_floor(self):
        import mgcp.server as srv

        assert srv.BRIDGE_MIN_SCORE == 0.55, (
            "BRIDGE_MIN_SCORE changed without the sweep. Re-run "
            "`python -m tests.bridge_benchmark --sweep` and update "
            "docs/bridge-measurement.md with the new table."
        )

    def test_the_harness_sweeps_through_the_shipped_value(self):
        from tests.bridge_benchmark import SHIPPED_MIN_SCORE, SWEEP

        assert SHIPPED_MIN_SCORE in SWEEP, (
            "the sweep must include the shipped value, or the table has no baseline"
        )

    def test_recorded_sweep_supports_the_chosen_value(self):
        """Read the evidence file, do not restate its numbers from memory."""
        import json
        from pathlib import Path

        path = Path("docs/bridge-results/bridge-sweep.json")
        if not path.exists():
            import pytest

            pytest.skip("sweep evidence not present in this checkout")

        rows = {r["min_score"]: r for r in json.loads(path.read_text())["thresholds"]}
        assert 0.55 in rows and 0.25 in rows and 0.60 in rows

        # No row may be void: the bridge only appends, so the searched results
        # must be identical at every threshold.
        for score, row in rows.items():
            assert not row["searched_results_differ"], f"threshold {score} is void"

        # The claim the shipped value rests on, checked against the file.
        assert rows[0.55]["useful_appends"] == rows[0.25]["useful_appends"], (
            "0.55 was chosen because it loses no labelled-useful append"
        )
        assert rows[0.55]["appended_slots"] < rows[0.25]["appended_slots"], (
            "0.55 was chosen because it appends strictly less"
        )
        assert rows[0.60]["useful_appends"] < rows[0.55]["useful_appends"], (
            "0.60 was rejected because it drops the useful append"
        )
