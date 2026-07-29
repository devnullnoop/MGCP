"""Write-time rejection of serialised tool-call envelopes, and the cleanup tool.

An agent that serialises its own tool-call wrapper into a parameter value writes
its envelope into the field value. Nothing rejected it, so the corruption is
permanent and it is embedded along with the real content. These tests pin three
things:

1. the detector fires on the envelope SHAPE and not on prose that merely
   discusses XML, angle brackets or parameter names;
2. the persistence write methods refuse such a write with a message an agent can
   act on, while READS of already-corrupt records keep working;
3. the cleanup script reports its repairs without touching the store unless
   asked, and repairs correctly when asked.

Envelope fixtures below are written with the U+2039/U+203A guillemets that the
existing sanitizer leaves behind - which is exactly what is sitting in the live
store today. `raw()` turns them back into real angle brackets so every case is
exercised in both forms.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from mgcp.models import Lesson, ProjectContext, Soliloquy, find_tool_call_envelope
from mgcp.persistence import LessonStore

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "clean_tool_call_envelopes.py"


def raw(text: str) -> str:
    """Turn the sanitizer's guillemets back into real angle brackets."""
    return text.replace("‹", "<").replace("›", ">")


def _load_cleaner():
    spec = importlib.util.spec_from_file_location("clean_tool_call_envelopes", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENVELOPES = [
    'Do the thing.‹/action› ‹parameter name="rationale"›because it broke‹/rationale›',
    'Do the thing.‹/parameter› ‹parameter name="tags"›["ui", "testing"]‹/parameter›',
    "Trailing wrapper only.‹/parameter› ‹/invoke›",
    '‹invoke name="mcp__mgcp__add_lesson"›',
    "‹function_calls›",
    '‹antml:parameter name="content"›spilled',
]

# Prose that talks about the syntax, or about XML generally. None of it is an
# envelope and none of it may be refused.
INNOCENT = [
    "Always close the tag: use </div> and <span> correctly.",
    "The parameter name must be snake_case, not camelCase.",
    "Guard the comparison: a < b and b > c must both hold.",
    "Never write `<parameter name=\"x\">` into a stored field value.",
    'Bad example:\n```xml\n<invoke name="Bash"><parameter name="cmd">ls</parameter></invoke>\n```\nDo not do that.',
    "Use <ClassName> generics when the type is known at the call site.",
    "Escape XML entities like &lt;invoke&gt; when quoting them in HTML.",
    "A function_calls block is what wraps tool use; do not hand-roll one.",
    "Set the invoke parameter to true so the hook fires.",
]


class TestDetection:
    @pytest.mark.parametrize("text", ENVELOPES)
    def test_envelope_detected_in_both_bracket_forms(self, text):
        assert find_tool_call_envelope(text) is not None, text
        assert find_tool_call_envelope(raw(text)) is not None, raw(text)

    @pytest.mark.parametrize("text", INNOCENT)
    def test_prose_about_xml_is_not_an_envelope(self, text):
        assert find_tool_call_envelope(text) is None, text

    def test_match_offsets_index_the_original_string(self):
        """Code-span masking must not shift offsets - the cleaner splits on them."""
        text = "Prefix `<parameter name=\"safe\">` middle ‹parameter name=\"spill\"›tail"
        match = find_tool_call_envelope(text)
        assert text[match.start():match.end()] == match.group(0)
        assert match.group(0) == '‹parameter name="spill"›'


class TestWriteRejection:
    @pytest.fixture
    async def store(self, tmp_path):
        store = LessonStore(str(tmp_path / "lessons.db"))
        yield store
        await store.close_pool()

    async def test_add_lesson_rejects_envelope_with_actionable_message(self, store):
        lesson = Lesson(
            id="spilled-lesson",
            trigger="when writing a lesson",
            action=raw(ENVELOPES[0]),
        )
        with pytest.raises(ValueError) as excinfo:
            await store.add_lesson(lesson)

        message = str(excinfo.value)
        # The message is a UI for a model: it must name the field, quote the
        # offending fragment, and say what to send instead.
        assert "action" in message
        assert 'parameter name="rationale"' in message
        assert "Re-send this field as plain text" in message
        assert "backticks" in message
        assert "clean_tool_call_envelopes.py" in message
        # And nothing was written.
        assert await store.get_lesson("spilled-lesson") is None

    async def test_add_lesson_accepts_a_lesson_about_xml(self, store):
        lesson = Lesson(
            id="xml-lesson",
            trigger="editing XML or discussing tool-call syntax",
            action=INNOCENT[3],
            rationale=INNOCENT[4],
            tags=["xml", "parameter", "invoke"],
        )
        await store.add_lesson(lesson)

        stored = await store.get_lesson("xml-lesson")
        assert stored is not None
        # Content survives intact - including the quoted syntax.
        assert "parameter name=" in stored.action
        assert "invoke" in stored.rationale

    async def test_envelope_rejected_on_every_agent_facing_write(self, store):
        poison = raw(ENVELOPES[1])

        lesson = Lesson(id="l1", trigger="t", action="clean action")
        await store.add_lesson(lesson)
        lesson.action = poison
        lesson.version = 2
        with pytest.raises(ValueError):
            await store.update_lesson(lesson)

        with pytest.raises(ValueError):
            await store.save_project_context(
                ProjectContext(
                    project_id="p1",
                    project_name="P",
                    project_path="/tmp/p1",
                    notes=poison,
                )
            )

        with pytest.raises(ValueError):
            await store.write_soliloquy(Soliloquy(content=poison))

    async def test_nested_and_list_fields_are_reached(self, store):
        with pytest.raises(ValueError) as excinfo:
            await store.save_project_context(
                ProjectContext(
                    project_id="p2",
                    project_name="P",
                    project_path="/tmp/p2",
                    recent_decisions=["clean decision", raw(ENVELOPES[2])],
                )
            )
        assert "recent_decisions[1]" in str(excinfo.value)

    async def test_reading_an_already_corrupt_record_still_works(self, store):
        """The guard is on writes only. The live store holds corrupt rows today;
        refusing to LOAD them would break every session that touches them."""
        await store.add_lesson(Lesson(id="legacy", trigger="t", action="clean"))
        async with store._connection(commit=True) as conn:
            await conn.execute(
                "UPDATE lessons SET action = ? WHERE id = 'legacy'", (raw(ENVELOPES[0]),)
            )

        stored = await store.get_lesson("legacy")
        assert stored is not None
        assert "rationale" in stored.action


class TestCleanupScript:
    """The dry run must report a count and a diff and change nothing."""

    @pytest.fixture
    def corrupt_db(self, tmp_path):
        cleaner = _load_cleaner()
        db_path = tmp_path / "lessons.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE lessons (
                id TEXT PRIMARY KEY, trigger TEXT, action TEXT,
                rationale TEXT, tags JSON DEFAULT '[]'
            );
            CREATE TABLE soliloquies (id INTEGER PRIMARY KEY, content TEXT);
            """
        )
        conn.execute(
            "INSERT INTO lessons VALUES (?, ?, ?, ?, ?)",
            (
                "spilled",
                "when shipping a version bump",
                raw(
                    "Update README in the same commit.‹/action›"
                    ' ‹parameter name="rationale"›The user caught the gap.‹/rationale›'
                    ' ‹parameter name="tags"›["docs", "release"]‹/parameter› ‹/invoke›'
                ),
                None,
                "[]",
            ),
        )
        conn.execute(
            "INSERT INTO lessons VALUES (?, ?, ?, ?, ?)",
            ("clean", "discussing tool-call syntax", INNOCENT[3], INNOCENT[4], '["xml"]'),
        )
        conn.execute(
            "INSERT INTO soliloquies VALUES (?, ?)",
            (1, raw("Good session.‹/parameter› ‹/invoke›")),
        )
        conn.commit()
        conn.close()
        return cleaner, db_path

    def test_dry_run_reports_and_writes_nothing(self, corrupt_db, capsys, monkeypatch):
        cleaner, db_path = corrupt_db
        before = db_path.read_bytes()

        monkeypatch.setattr(
            "sys.argv", ["clean", "--data-dir", str(db_path.parent)]
        )
        assert cleaner.main() == 0

        out = capsys.readouterr().out
        assert "Affected records: 2" in out
        assert "lessons: 1" in out and "soliloquies: 1" in out
        assert "DRY RUN - nothing written" in out
        # A diff, not just a count: the removed envelope is shown.
        assert 'parameter name="rationale"' in out
        assert "RECOVER from spill" in out
        # Byte-identical afterwards.
        assert db_path.read_bytes() == before

    def test_apply_repairs_and_recovers_spilled_fields(self, corrupt_db):
        cleaner, db_path = corrupt_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        findings = cleaner.scan(conn)
        repaired = cleaner.apply(conn, findings)

        # Repaired rows that carry a vector are reported back so the caller can
        # rebuild the embedding; a soliloquy has no vector, so it is not listed.
        assert repaired == {"lessons": ["spilled"], "project_contexts": []}
        row = conn.execute("SELECT * FROM lessons WHERE id = 'spilled'").fetchone()
        assert find_tool_call_envelope(row["action"]) is None
        assert row["action"] == "Update README in the same commit."
        # The spilled sub-fields land in their own columns, which were empty.
        assert row["rationale"] == "The user caught the gap."
        assert json.loads(row["tags"]) == ["docs", "release"]

        # The lesson that only talks about the syntax is untouched.
        clean = conn.execute("SELECT * FROM lessons WHERE id = 'clean'").fetchone()
        assert clean["action"] == INNOCENT[3]

        assert cleaner.scan(conn) == []
        conn.close()

    def test_apply_against_the_live_store_requires_an_explicit_flag(
        self, corrupt_db, monkeypatch, capsys
    ):
        cleaner, db_path = corrupt_db
        monkeypatch.setattr(cleaner, "LIVE_DATA_DIR", db_path.parent)
        monkeypatch.setattr(
            "sys.argv", ["clean", "--data-dir", str(db_path.parent), "--apply"]
        )
        before = db_path.read_bytes()

        assert cleaner.main() == 2
        assert "Refusing to --apply against the live store" in capsys.readouterr().err
        assert db_path.read_bytes() == before
