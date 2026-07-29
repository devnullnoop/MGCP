"""The soliloquy is one continuous inner voice, but the read is project-aware.

Storage stays global on purpose: the agent's message to its future self is not
partitioned by codebase. What broke was the READ — session start on one project
surfaced the newest entry from anywhere, so work on BoltMob opened with a
reflection about a 3D flight sim in another language.

These tests drive the real store and the real MCP tool functions. No mocks
between the claim and the assertion.
"""

import pytest

import mgcp.server as server_module
from mgcp.models import ProjectContext
from mgcp.persistence import LessonStore
from mgcp.server import read_soliloquy, write_soliloquy

ALPHA_PATH = "/tmp/projects/alpha"
BETA_PATH = "/tmp/projects/beta"


@pytest.fixture
async def store(tmp_path, monkeypatch):
    """A real store with two projects, injected into the server globals.

    Only the store is needed: the soliloquy tools touch nothing else.
    CLAUDE_PROJECT_DIR is cleared so an explicit project_path is the only
    thing steering the resolution.
    """
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    s = LessonStore(db_path=str(tmp_path / "test.db"))

    for path, name, sessions in ((ALPHA_PATH, "alpha", 12), (BETA_PATH, "beta", 3)):
        await s.save_project_context(
            ProjectContext(
                project_id=name + "-id",
                project_name=name,
                project_path=path,
                session_count=sessions,
            )
        )

    prev_store, prev_init = server_module._store, server_module._initialized
    server_module._store = s
    server_module._initialized = True
    yield s
    server_module._store = prev_store
    server_module._initialized = prev_init
    await s.close_pool()


async def test_write_tags_the_entry_with_its_project(store):
    """Storage is global; the row still records where the thought was had."""
    await write_soliloquy(content="alpha thoughts", project_path=ALPHA_PATH)

    entry, project_id = await store.read_latest_soliloquy()
    assert entry.content == "alpha thoughts"
    assert project_id == "alpha-id"


async def test_session_number_is_this_projects_not_the_global_max(store):
    """beta is on session 3. alpha is on 12. A beta reflection stamped
    'Session 12' is a false claim about beta's history."""
    await write_soliloquy(content="beta thoughts", project_path=BETA_PATH)

    entry, _ = await store.read_latest_soliloquy()
    assert entry.session_number == 3


async def test_read_prefers_this_projects_own_entry(store):
    """alpha writes, then beta writes. alpha must still read alpha."""
    await write_soliloquy(content="alpha thoughts", project_path=ALPHA_PATH)
    await write_soliloquy(content="beta thoughts", project_path=BETA_PATH)

    out = await read_soliloquy(project_path=ALPHA_PATH)
    assert "alpha thoughts" in out
    assert "beta thoughts" not in out
    assert "another project" not in out


async def test_foreign_fallback_is_labelled_not_silent(store):
    """beta has nothing of its own. It still gets alpha's entry — one
    continuous voice — but it is told whose voice it is."""
    await write_soliloquy(content="alpha thoughts", project_path=ALPHA_PATH)

    out = await read_soliloquy(project_path=BETA_PATH)
    assert "alpha thoughts" in out
    assert "from another project: alpha" in out
    assert "no soliloquy of its own yet" in out


async def test_untagged_legacy_entries_are_labelled_too(store):
    """The 52 entries written before tagging existed carry project_id NULL.
    They must not pass themselves off as this project's own."""
    from mgcp.models import Soliloquy

    await store.write_soliloquy(Soliloquy(content="legacy thoughts"))

    out = await read_soliloquy(project_path=ALPHA_PATH)
    assert "legacy thoughts" in out
    assert "untagged earlier session" in out


async def test_multi_entry_read_marks_each_foreign_entry(store):
    """limit>1 lists across projects by design; every foreign line is marked."""
    await write_soliloquy(content="alpha thoughts", project_path=ALPHA_PATH)
    await write_soliloquy(content="beta thoughts", project_path=BETA_PATH)

    out = await read_soliloquy(limit=5, project_path=ALPHA_PATH)
    headings = [ln for ln in out.splitlines() if ln.startswith("###")]
    assert len(headings) == 2
    # Exactly one of the two is foreign to alpha, and it says so.
    assert sum("from another project: beta" in ln for ln in headings) == 1


async def test_empty_journal_still_reports_empty(store):
    out = await read_soliloquy(project_path=ALPHA_PATH)
    assert "No soliloquy found yet" in out
