"""Tests for skill compilation: maturity, generation, graduation, drift."""

import os
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import CompiledSkill, Example, Lesson, Relationship
from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore
from mgcp.skill_compiler import (
    assess_maturity,
    compile_community,
    detect_drift,
    generate_skill_body,
    generate_skill_description,
    graduate_lessons,
    list_compilation_candidates,
    ungraduate_lessons,
)


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        yield db_path


@pytest.fixture
def temp_qdrant():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_skills_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def store(temp_db):
    return LessonStore(temp_db)


@pytest.fixture
def vector_store(temp_qdrant):
    return QdrantVectorStore(persist_path=temp_qdrant)


def _make_lesson(
    id: str,
    trigger: str = "test trigger",
    action: str = "test action",
    tags: list[str] | None = None,
    usage_count: int = 5,
    version: int = 1,
    graduated_to: str | None = None,
    examples: list[Example] | None = None,
) -> Lesson:
    return Lesson(
        id=id,
        trigger=trigger,
        action=action,
        rationale="test rationale",
        tags=tags or ["test"],
        usage_count=usage_count,
        version=version,
        graduated_to=graduated_to,
        examples=examples or [],
    )


def _make_community(
    community_id: str = "test-community",
    members: list[str] | None = None,
    summary_title: str = "Test Community",
    size: int | None = None,
) -> dict:
    members = members or ["a", "b", "c", "d"]
    return {
        "community_id": community_id,
        "members": members,
        "top_members": members[:3],
        "size": size if size is not None else len(members),
        "summary_title": summary_title,
        "aggregate_tags": {"test": 4},
        "internal_edges": 3,
        "external_edges": 1,
        "density": 0.5,
    }


# ===========================================================================
# Maturity Assessment
# ===========================================================================


class TestMaturityAssessment:
    def test_too_small(self):
        """Communities with fewer than 4 lessons are not ready."""
        community = _make_community(members=["a", "b"])
        lessons = [_make_lesson("a"), _make_lesson("b")]
        result = assess_maturity(community, lessons)
        assert not result.ready
        assert result.score == 0.0
        assert any("Too small" in r for r in result.reasons)

    def test_ready_with_enough_usage_and_summary(self):
        """Community meeting all criteria should be ready."""
        community = _make_community(members=["a", "b", "c", "d"])
        lessons = [
            _make_lesson("a", usage_count=5, version=2),
            _make_lesson("b", usage_count=4, version=2),
            _make_lesson("c", usage_count=3, version=1),
            _make_lesson("d", usage_count=2, version=1),
        ]
        result = assess_maturity(community, lessons, summary_exists=True)
        assert result.ready
        assert result.score >= 0.5

    def test_low_usage_reduces_score(self):
        """Low usage should reduce the maturity score."""
        community = _make_community()
        lessons = [_make_lesson(f"l{i}", usage_count=1) for i in range(4)]
        result = assess_maturity(community, lessons, summary_exists=True)
        # Should still have some score from size + summary, but lower
        assert any("Low usage" in r for r in result.reasons)

    def test_graduated_members_reduce_score(self):
        """Already-graduated members should reduce the score."""
        community = _make_community()
        lessons = [
            _make_lesson("a", graduated_to="some-skill"),
            _make_lesson("b"),
            _make_lesson("c"),
            _make_lesson("d"),
        ]
        result = assess_maturity(community, lessons)
        assert any("already graduated" in r for r in result.reasons)

    def test_no_summary_noted(self):
        community = _make_community()
        lessons = [_make_lesson(f"l{i}") for i in range(4)]
        result = assess_maturity(community, lessons, summary_exists=False)
        assert any("No community summary" in r for r in result.reasons)


# ===========================================================================
# Description Generation
# ===========================================================================


class TestDescriptionGeneration:
    def test_aggregates_triggers(self):
        lessons = [
            _make_lesson("a", trigger="git commit, branching"),
            _make_lesson("b", trigger="version control, branching strategy"),
            _make_lesson("c", trigger="git workflow, merge conflicts"),
        ]
        description = generate_skill_description(lessons)
        assert len(description) > 0
        assert "," in description  # Multiple keywords

    def test_handles_empty_triggers(self):
        lessons = [_make_lesson("a", trigger="")]
        description = generate_skill_description(lessons)
        assert description == "general guidance"


# ===========================================================================
# Body Generation
# ===========================================================================


class TestBodyGeneration:
    def test_valid_markdown_with_frontmatter(self):
        lessons = [
            _make_lesson("a", action="Always check types", tags=["typing"]),
            _make_lesson("b", action="Use strict mode", tags=["typing"]),
            _make_lesson("c", action="Write unit tests", tags=["testing"]),
        ]
        body = generate_skill_body(
            title="Test Skill",
            summary="A test skill for testing",
            lessons=lessons,
            community_id="abc123",
            skill_name="test-skill",
        )
        assert body.startswith("---")
        assert "name: test-skill" in body
        assert "user-invocable: false" in body
        assert "# Test Skill" in body
        assert "## Key Practices" in body
        assert "## When This Applies" in body
        assert "## Common Pitfalls" in body
        assert "Compiled from MGCP community abc123" in body

    def test_includes_bad_examples_as_pitfalls(self):
        lessons = [
            _make_lesson(
                "a",
                examples=[
                    Example(label="bad", code="dont_do_this()", explanation="It breaks"),
                ],
            ),
        ]
        body = generate_skill_body("T", "S", lessons, "c1", "sk")
        assert "dont_do_this()" in body
        assert "It breaks" in body

    def test_groups_by_tags(self):
        lessons = [
            _make_lesson("a", action="Action A", tags=["security"]),
            _make_lesson("b", action="Action B", tags=["testing"]),
        ]
        body = generate_skill_body("T", "S", lessons, "c1", "sk")
        assert "### Security" in body
        assert "### Testing" in body


# ===========================================================================
# Graduation & Persistence
# ===========================================================================


class TestGraduation:
    @pytest.mark.asyncio
    async def test_graduate_marks_lessons(self, store, vector_store):
        """Graduating lessons sets graduated_to field."""
        lessons = [_make_lesson(f"grad-{i}") for i in range(3)]
        for l in lessons:
            await store.add_lesson(l)
            vector_store.add_lesson(l)

        count = await graduate_lessons(
            lesson_ids=[l.id for l in lessons],
            skill_name="test-skill",
            store=store,
            vector_store=vector_store,
        )
        assert count == 3

        for l in lessons:
            updated = await store.get_lesson(l.id)
            assert updated.graduated_to == "test-skill"

    @pytest.mark.asyncio
    async def test_graduated_filtered_from_search(self, store, vector_store):
        """Graduated lessons should not appear in default search."""
        # Add a graduated and non-graduated lesson
        active = _make_lesson("active-lesson", trigger="python coding patterns")
        graduated = _make_lesson(
            "graduated-lesson",
            trigger="python coding patterns",
            graduated_to="some-skill",
        )
        await store.add_lesson(active)
        await store.add_lesson(graduated)
        vector_store.add_lesson(active)
        vector_store.add_lesson(graduated)

        # Default search should exclude graduated
        results = vector_store.search("python coding", include_graduated=False)
        result_ids = [r[0] for r in results]
        assert "active-lesson" in result_ids
        assert "graduated-lesson" not in result_ids

        # Explicit include should return both
        results_all = vector_store.search("python coding", include_graduated=True)
        result_ids_all = [r[0] for r in results_all]
        assert "active-lesson" in result_ids_all
        assert "graduated-lesson" in result_ids_all

    @pytest.mark.asyncio
    async def test_ungraduate_restores_lessons(self, store, vector_store):
        """Ungraduating restores lessons to active search."""
        lessons = [_make_lesson(f"ungrad-{i}", trigger="unique trigger text") for i in range(2)]
        for l in lessons:
            await store.add_lesson(l)
            vector_store.add_lesson(l)

        # Graduate
        await graduate_lessons(
            [l.id for l in lessons], "test-skill", store, vector_store
        )

        # Verify graduated
        for l in lessons:
            updated = await store.get_lesson(l.id)
            assert updated.graduated_to == "test-skill"

        # Ungraduate
        count = await ungraduate_lessons("test-skill", store, vector_store)
        assert count == 2

        # Verify restored
        for l in lessons:
            restored = await store.get_lesson(l.id)
            assert restored.graduated_to is None


# ===========================================================================
# Compiled Skill Persistence
# ===========================================================================


class TestCompiledSkillPersistence:
    @pytest.mark.asyncio
    async def test_save_and_get(self, store):
        skill = CompiledSkill(
            skill_name="test-skill",
            community_id="comm-1",
            member_ids=["a", "b", "c"],
            skill_path="/tmp/test-skill/SKILL.md",
        )
        await store.save_compiled_skill(skill)

        retrieved = await store.get_compiled_skill("test-skill")
        assert retrieved is not None
        assert retrieved.skill_name == "test-skill"
        assert retrieved.member_ids == ["a", "b", "c"]
        assert retrieved.version == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_version(self, store):
        skill = CompiledSkill(
            skill_name="test-skill",
            community_id="comm-1",
            member_ids=["a", "b"],
            skill_path="/tmp/test/SKILL.md",
        )
        await store.save_compiled_skill(skill)

        skill.version = 2
        skill.member_ids = ["a", "b", "c"]
        await store.save_compiled_skill(skill)

        retrieved = await store.get_compiled_skill("test-skill")
        assert retrieved.version == 2
        assert len(retrieved.member_ids) == 3

    @pytest.mark.asyncio
    async def test_delete(self, store):
        skill = CompiledSkill(
            skill_name="to-delete",
            community_id="comm-1",
            member_ids=["a"],
            skill_path="/tmp/test/SKILL.md",
        )
        await store.save_compiled_skill(skill)
        assert await store.delete_compiled_skill("to-delete")
        assert await store.get_compiled_skill("to-delete") is None

    @pytest.mark.asyncio
    async def test_get_all(self, store):
        for i in range(3):
            skill = CompiledSkill(
                skill_name=f"skill-{i}",
                community_id=f"comm-{i}",
                member_ids=[f"l{i}"],
                skill_path=f"/tmp/skill-{i}/SKILL.md",
            )
            await store.save_compiled_skill(skill)

        all_skills = await store.get_all_compiled_skills()
        assert len(all_skills) == 3


# ===========================================================================
# Drift Detection
# ===========================================================================


class TestDriftDetection:
    @pytest.mark.asyncio
    async def test_detects_refined_lesson(self, store):
        """Lessons refined after compilation trigger drift."""
        lesson = _make_lesson("drift-test")
        await store.add_lesson(lesson)

        compiled_time = datetime.now(UTC) - timedelta(hours=1)
        skill = CompiledSkill(
            skill_name="test-skill",
            community_id="comm-1",
            member_ids=["drift-test"],
            skill_path="/tmp/test/SKILL.md",
            compiled_at=compiled_time,
        )
        await store.save_compiled_skill(skill)

        # Refine the lesson (update last_refined to now)
        lesson_obj = await store.get_lesson("drift-test")
        lesson_obj.last_refined = datetime.now(UTC)
        lesson_obj.version = 2
        await store.update_lesson(lesson_obj)

        drift = await detect_drift(store)
        assert len(drift) == 1
        assert drift[0]["reason"] == "refined_after_compilation"

    @pytest.mark.asyncio
    async def test_detects_deleted_lesson(self, store):
        """Deleted member lessons trigger drift."""
        lesson = _make_lesson("will-delete")
        await store.add_lesson(lesson)

        skill = CompiledSkill(
            skill_name="test-skill",
            community_id="comm-1",
            member_ids=["will-delete"],
            skill_path="/tmp/test/SKILL.md",
        )
        await store.save_compiled_skill(skill)

        await store.delete_lesson("will-delete")

        drift = await detect_drift(store)
        assert len(drift) == 1
        assert drift[0]["reason"] == "deleted"

    @pytest.mark.asyncio
    async def test_no_drift_when_clean(self, store):
        """No drift when nothing changed."""
        lesson = _make_lesson("clean")
        lesson.last_refined = datetime.now(UTC) - timedelta(hours=2)
        await store.add_lesson(lesson)

        skill = CompiledSkill(
            skill_name="clean-skill",
            community_id="comm-1",
            member_ids=["clean"],
            skill_path="/tmp/test/SKILL.md",
            compiled_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await store.save_compiled_skill(skill)

        drift = await detect_drift(store)
        assert len(drift) == 0


# ===========================================================================
# Full Compilation Pipeline
# ===========================================================================


def _build_graph_with_community(lessons: list[Lesson]) -> LessonGraph:
    """Build a graph where all lessons are linked, forming a community.

    Adds relationships to lessons so that add_lesson creates edges.
    """
    # Add cross-references so add_lesson creates edges
    for i, lesson in enumerate(lessons):
        for j, other in enumerate(lessons):
            if i < j:
                lesson.relationships.append(
                    Relationship(target=other.id, type="related", weight=0.5)
                )

    graph = LessonGraph()
    for lesson in lessons:
        graph.add_lesson(lesson)
    return graph


class TestCompilePipeline:

    @pytest.mark.asyncio
    async def test_compile_writes_file(self, store, vector_store, temp_skills_dir):
        """Full compilation writes SKILL.md to disk."""
        lessons = [
            _make_lesson(f"compile-{i}", usage_count=5, version=2, tags=["testing"])
            for i in range(5)
        ]
        for l in lessons:
            await store.add_lesson(l)
            vector_store.add_lesson(l)

        graph = _build_graph_with_community(lessons)

        # Get the community ID
        communities = graph.detect_communities()
        assert len(communities) > 0
        community_id = communities[0]["community_id"]

        result = await compile_community(
            community_id=community_id,
            store=store,
            vector_store=vector_store,
            graph=graph,
            skill_name="test-compile",
            skills_dir=temp_skills_dir,
            force=True,  # Skip maturity (no summary)
        )

        assert "error" not in result
        assert result["skill_name"] == "test-compile"
        assert result["graduated_count"] == 5

        # Verify file exists
        skill_path = os.path.join(temp_skills_dir, "test-compile", "SKILL.md")
        assert os.path.exists(skill_path)
        content = open(skill_path).read()
        assert "user-invocable: false" in content

        # Verify REM action was recorded for effectiveness tracking
        actions = await store.get_rem_action_history(target_id=community_id)
        assert len(actions) == 1
        assert actions[0].action_type == "skill_compile"
        assert actions[0].target_type == "community"
        assert actions[0].action_detail["skill_name"] == "test-compile"
        assert "member_baselines" in actions[0].baseline_snapshot
        assert actions[0].measured_at is None  # Not measured yet

    @pytest.mark.asyncio
    async def test_dry_run_no_side_effects(self, store, vector_store, temp_skills_dir):
        """Dry run should not write files or graduate lessons."""
        lessons = [_make_lesson(f"dry-{i}") for i in range(5)]
        for l in lessons:
            await store.add_lesson(l)
            vector_store.add_lesson(l)

        graph = _build_graph_with_community(lessons)
        communities = graph.detect_communities()
        community_id = communities[0]["community_id"]

        result = await compile_community(
            community_id=community_id,
            store=store,
            vector_store=vector_store,
            graph=graph,
            skills_dir=temp_skills_dir,
            force=True,
            dry_run=True,
        )

        assert result["dry_run"] is True

        # No file written
        assert not os.path.exists(
            os.path.join(temp_skills_dir, result["skill_name"], "SKILL.md")
        )

        # No lessons graduated
        for l in lessons:
            lesson = await store.get_lesson(l.id)
            assert lesson.graduated_to is None

        # No REM action recorded for dry run
        all_actions = await store.get_rem_action_history()
        assert len(all_actions) == 0

    @pytest.mark.asyncio
    async def test_recompile_increments_version(self, store, vector_store, temp_skills_dir):
        """Recompiling a skill increments the version."""
        lessons = [
            _make_lesson(f"recomp-{i}", usage_count=5, version=2)
            for i in range(5)
        ]
        for l in lessons:
            await store.add_lesson(l)
            vector_store.add_lesson(l)

        graph = _build_graph_with_community(lessons)
        communities = graph.detect_communities()
        community_id = communities[0]["community_id"]

        # First compile
        result1 = await compile_community(
            community_id=community_id,
            store=store,
            vector_store=vector_store,
            graph=graph,
            skill_name="recomp-skill",
            skills_dir=temp_skills_dir,
            force=True,
        )
        assert result1["version"] == 1

        # Ungraduate to allow recompilation
        await ungraduate_lessons("recomp-skill", store, vector_store)

        # Recompile
        result2 = await compile_community(
            community_id=community_id,
            store=store,
            vector_store=vector_store,
            graph=graph,
            skill_name="recomp-skill",
            skills_dir=temp_skills_dir,
            force=True,
        )
        assert result2["version"] == 2

        # Verify two REM actions recorded (one per compile)
        actions = await store.get_rem_action_history(target_id=community_id)
        assert len(actions) == 2
        assert all(a.action_type == "skill_compile" for a in actions)


# ===========================================================================
# Compilation Candidates
# ===========================================================================


class TestCompilationCandidates:
    @pytest.mark.asyncio
    async def test_finds_mature_communities(self, store):
        """Communities with enough lessons and usage should be found."""
        from mgcp.models import CommunitySummary

        # Create a set of well-used lessons
        lessons = [
            _make_lesson(f"cand-{i}", usage_count=5, version=2, tags=["test"])
            for i in range(5)
        ]
        for l in lessons:
            await store.add_lesson(l)

        # Build graph with community
        graph = _build_graph_with_community(lessons)

        # Add community summary
        communities = graph.detect_communities()
        if communities:
            comm_id = communities[0]["community_id"]
            summary = CommunitySummary(
                community_id=comm_id,
                title="Test Candidates",
                summary="A test community",
                member_ids=[l.id for l in lessons],
                member_count=len(lessons),
            )
            await store.save_community_summary(summary)

        candidates = await list_compilation_candidates(store, graph, min_maturity=0.3)
        assert len(candidates) > 0
        assert candidates[0].member_count >= 4


# ===========================================================================
# Lesson Model
# ===========================================================================


class TestLessonGraduatedField:
    def test_graduated_to_default_none(self):
        lesson = Lesson(id="test", trigger="t", action="a")
        assert lesson.graduated_to is None

    def test_graduated_to_set(self):
        lesson = Lesson(id="test", trigger="t", action="a", graduated_to="my-skill")
        assert lesson.graduated_to == "my-skill"

    @pytest.mark.asyncio
    async def test_graduated_persisted(self, store):
        """graduated_to field roundtrips through SQLite."""
        lesson = _make_lesson("persist-grad", graduated_to="saved-skill")
        await store.add_lesson(lesson)

        retrieved = await store.get_lesson("persist-grad")
        assert retrieved.graduated_to == "saved-skill"

    @pytest.mark.asyncio
    async def test_graduated_updated(self, store):
        """graduated_to can be updated."""
        lesson = _make_lesson("update-grad")
        await store.add_lesson(lesson)

        lesson.graduated_to = "new-skill"
        await store.update_lesson(lesson)

        retrieved = await store.get_lesson("update-grad")
        assert retrieved.graduated_to == "new-skill"
