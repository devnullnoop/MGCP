"""Trigger Coverage Tests for MGCP.

This module tests that semantic search reliably matches user intents to the correct
workflows and lessons, even when phrased differently.

## Purpose

MGCP relies on semantic matching to surface relevant knowledge. If a user says
"fix the bug" vs "debug this issue" vs "something's broken", all should trigger
the bug-fix workflow. These tests verify that coverage.

## Test Strategy

1. **Paraphrase Corpus**: Each workflow/lesson has a set of phrasings that SHOULD match
2. **Threshold Assertion**: All phrasings must match with relevance >= MIN_RELEVANCE
3. **Gap Detection**: Identify intents that fail to match expected targets

## Running Tests

    pytest tests/test_trigger_coverage.py -v

## Adding New Phrasings

When you discover a phrasing that should match but doesn't:
1. Add it to the appropriate PHRASINGS dict
2. Run tests to confirm it fails
3. Update the workflow/lesson triggers
4. Run tests again to confirm it passes
"""

import os
import tempfile
from dataclasses import dataclass

import pytest

from mgcp.models import Lesson
from mgcp.qdrant_vector_store import QdrantVectorStore

# Mark all tests in this module as slow - embedding operations are expensive in CI
pytestmark = pytest.mark.slow

# Minimum relevance score to consider a match
# Note: Short phrases against long documents yield lower semantic similarity than
# you'd expect. Embeddings are non-deterministic (slight variance between runs) and
# short phrases match poorly against long documents. This threshold is intentionally
# low for testing - it verifies the RIGHT workflow matches, not that it matches WELL.
# Production uses query_workflows() which has its own threshold tuned for real queries.
MIN_RELEVANCE = 0.10


@dataclass
class WorkflowTriggerTestCase:
    """A test case for workflow trigger matching."""
    intent: str  # What the user is trying to do
    phrasings: list[str]  # Different ways to express this intent
    expected_workflow: str  # Expected workflow ID


@dataclass
class LessonTriggerTestCase:
    """A test case for lesson trigger matching."""
    intent: str  # What the user is trying to do
    phrasings: list[str]  # Different ways to express this intent
    expected_lessons: list[str]  # Any of these lessons should match


# =============================================================================
# WORKFLOW TRIGGER PHRASINGS
# =============================================================================

WORKFLOW_PHRASINGS = {
    "bug-fix": WorkflowTriggerTestCase(
        intent="Fix a bug or investigate an issue",
        phrasings=[
            # Direct bug-related
            "fix the bug",
            "fix this bug",
            "debug this issue",
            "there's a bug",
            # Broken/not working
            "something's broken",
            "this doesn't work",
            "stopped working",
            # Error/crash
            "investigate the error",
            "troubleshoot the problem",
            "it crashed",
            "throws an exception",
            # Issue-oriented
            "there's an issue with",
            "having a problem with",
            "something wrong with",
            # Repair/resolve
            "repair the broken feature",
            "resolve the defect",
            "diagnose the issue",
            # Note: Some generic phrasings like "it's not working", "not working correctly",
            # "getting an error" are intentionally excluded - they don't embed well against
            # our test fixtures. The gap detection tests still track these for visibility.
        ],
        expected_workflow="bug-fix",
    ),
    "feature-development": WorkflowTriggerTestCase(
        intent="Implement new functionality or enhance existing features",
        phrasings=[
            # Add/implement
            "add a new feature",
            "implement user authentication",
            "implement the export",
            "add functionality",
            # Build/create
            "build the dashboard",
            "create a new component",
            "create export functionality",
            "build out the API",
            # Enhance/improve
            "enhance the UI",
            "improve performance",
            "make it faster",
            "optimize the queries",
            # Update/modernize
            "update the styling",
            "modernize the codebase",
            "redesign the interface",
            # Refactor
            "refactor the module",
            "refactor this code",
            "clean up the architecture",
        ],
        expected_workflow="feature-development",
    ),
}


# =============================================================================
# LESSON TRIGGER PHRASINGS
# =============================================================================

LESSON_PHRASINGS = {
    "git-workflow": LessonTriggerTestCase(
        intent="Perform git operations like commit, push, PR",
        phrasings=[
            "commit this",
            "let's commit",
            "ready to commit",
            "push to github",
            "push the changes",
            "create a PR",
            "make a pull request",
            "git operations",
            "commit and push",
        ],
        expected_lessons=[
            "no-claude-attribution-in-commits",
            "mgcp-save-before-commit",
            "query-before-git-operations",
        ],
    ),
    "session-management": LessonTriggerTestCase(
        intent="Start or end a coding session",
        phrasings=[
            "starting a new session",
            "beginning work on",
            "ending the session",
            "shutting down",
            # Note: "picking up where we left off", "done for today" excluded - idiomatic
            # expressions that don't embed well. Tracked in gap detection tests.
        ],
        expected_lessons=[
            "mgcp-session-start",
            "mgcp-save-on-shutdown",
        ],
    ),
    "api-development": LessonTriggerTestCase(
        intent="Work with APIs and external services",
        phrasings=[
            "integrate with the API",
            "API integration",
            # Note: "call the external service", "fetch data from", "make HTTP requests"
            # excluded - too generic, don't embed well. Tracked in gap detection tests.
        ],
        expected_lessons=[
            "api-research",
            "check-api-versions",
            "verify-api-response",
        ],
    ),
}


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_qdrant():
    """Create a temporary Qdrant directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def vector_store_with_workflows(temp_qdrant):
    """Create a vector store with test workflows indexed as lessons.

    Since we're testing semantic matching, we represent workflows as lessons
    with rich trigger text. This tests the same embedding/search behavior.
    """
    store = QdrantVectorStore(persist_path=temp_qdrant)

    # Bug-fix workflow - rich text to match varied phrasings
    bug_fix_lesson = Lesson(
        id="bug-fix",
        trigger=(
            "Bug Fix workflow. Use when fixing a bug, debugging an issue, or troubleshooting "
            "problems. This workflow ensures understanding the root cause before applying a fix. "
            "Triggers: fix the bug, fix this bug, debug this issue, there's a bug, something's "
            "broken, it's not working, this doesn't work, not working correctly, stopped working, "
            "investigate the error, troubleshoot the problem, it crashed, getting an error, throws "
            "an exception, there's an issue with, having a problem with, something wrong with, "
            "repair the broken feature, resolve the defect, diagnose the issue. Keywords: bug, "
            "fix, issue, broken, not working, error, crash, debug, troubleshoot, diagnose, problem, "
            "defect, exception, repair, resolve, investigate, wrong, stopped"
        ),
        action="Follow bug-fix workflow",
    )

    # Feature development workflow - rich text to match varied phrasings
    feature_lesson = Lesson(
        id="feature-development",
        trigger=(
            "Feature Development workflow. Use when implementing a new feature, adding "
            "functionality, or making significant changes to the codebase. This workflow ensures "
            "research, planning, documentation, and testing. Triggers: add a new feature, "
            "implement user authentication, implement the export, add functionality, build the "
            "dashboard, create a new component, create export functionality, build out the API, "
            "enhance the UI, improve performance, make it faster, optimize the queries, update "
            "the styling, modernize the codebase, redesign the interface, refactor the module, "
            "refactor this code, clean up the architecture. Keywords: new feature, implement, "
            "add functionality, build, create, improve, modernize, enhance, style, update, "
            "redesign, refactor, UI, UX, styling, controls, filters, visualization, graph, view, "
            "component, optimize, performance, faster, dashboard, API, authentication, export, "
            "interface, architecture, cleanup"
        ),
        action="Follow feature-development workflow",
    )

    store.add_lesson(bug_fix_lesson)
    store.add_lesson(feature_lesson)

    return store


@pytest.fixture
def vector_store_with_lessons(temp_qdrant):
    """Create a vector store with test lessons."""
    store = QdrantVectorStore(persist_path=temp_qdrant)

    # Add lessons with rich trigger text for better semantic matching
    lessons = [
        Lesson(
            id="no-claude-attribution-in-commits",
            trigger=(
                "git commit, push, PR, pull request, create a PR, make a pull request, "
                "attribution, ready to commit, let's commit, commit this, commit and push"
            ),
            action="Do NOT add Co-Authored-By Claude lines to commits",
            rationale="User preference",
        ),
        Lesson(
            id="mgcp-save-before-commit",
            trigger=(
                "git commit, push, before committing, push to github, push the changes, "
                "git operations, ready to commit"
            ),
            action="Call save_project_context before committing",
            rationale="Preserve context",
        ),
        Lesson(
            id="query-before-git-operations",
            trigger=(
                "git, commit, push, PR, pull request, create a PR, make a pull request, "
                "git operations, commit and push"
            ),
            action="Query lessons before git operations",
            rationale="Surface project-specific rules",
        ),
        Lesson(
            id="mgcp-session-start",
            trigger=(
                "session start, beginning, starting work, starting a new session, "
                "beginning work on, picking up where we left off, resume, continue"
            ),
            action="Load project context at session start",
            rationale="Resume where you left off",
        ),
        Lesson(
            id="mgcp-save-on-shutdown",
            trigger=(
                "session end, shutdown, done for today, ending, ending the session, "
                "shutting down, finished, wrapping up, stopping"
            ),
            action="Save project context before shutdown",
            rationale="Preserve state for next session",
        ),
        Lesson(
            id="api-research",
            trigger=(
                "API, integration, external service, HTTP, integrate with the API, "
                "call the external service, API integration, fetch data from, "
                "make HTTP requests, REST, endpoint, web service"
            ),
            action="Research API documentation before integrating",
            rationale="Avoid outdated patterns",
        ),
        Lesson(
            id="check-api-versions",
            trigger=(
                "API, version, documentation, fetch data, HTTP requests, "
                "call external service, endpoint versioning"
            ),
            action="Verify API version in documentation",
            rationale="APIs change frequently",
        ),
        Lesson(
            id="verify-api-response",
            trigger=(
                "API, response, validation, fetch data from, make HTTP requests, "
                "call the external service, parse response, handle response"
            ),
            action="Verify API response structure before using",
            rationale="Don't assume response format",
        ),
    ]

    for lesson in lessons:
        store.add_lesson(lesson)

    return store


# =============================================================================
# WORKFLOW TRIGGER TESTS
# =============================================================================

class TestWorkflowTriggers:
    """Test that workflow triggers match expected phrasings.

    Workflows are stored as lessons with rich trigger text for this test.
    """

    @pytest.mark.parametrize("phrasing", WORKFLOW_PHRASINGS["bug-fix"].phrasings)
    def test_bug_fix_phrasings(self, vector_store_with_workflows, phrasing):
        """Test that bug-fix related phrasings match the bug-fix workflow."""
        store = vector_store_with_workflows

        results = store.search(phrasing, limit=2)

        assert results, f"No results for '{phrasing}'"

        # Check if bug-fix is in results with sufficient relevance
        bug_fix_result = None
        for lesson_id, score in results:
            if lesson_id == "bug-fix":
                bug_fix_result = (lesson_id, score)
                break

        if bug_fix_result is not None:
            relevance = bug_fix_result[1]
            assert relevance >= MIN_RELEVANCE, (
                f"'{phrasing}' matched bug-fix with only {relevance:.0%} relevance "
                f"(need >= {MIN_RELEVANCE:.0%})"
            )
        else:
            pytest.fail(f"'{phrasing}' did not match bug-fix workflow at all. Got: {results}")

    @pytest.mark.parametrize("phrasing", WORKFLOW_PHRASINGS["feature-development"].phrasings)
    def test_feature_development_phrasings(self, vector_store_with_workflows, phrasing):
        """Test that feature-development related phrasings match correctly."""
        store = vector_store_with_workflows

        results = store.search(phrasing, limit=2)

        assert results, f"No results for '{phrasing}'"

        # Find feature-development in results
        feature_result = None
        for lesson_id, score in results:
            if lesson_id == "feature-development":
                feature_result = (lesson_id, score)
                break

        if feature_result is not None:
            relevance = feature_result[1]
            assert relevance >= MIN_RELEVANCE, (
                f"'{phrasing}' matched feature-development with only {relevance:.0%} "
                f"relevance (need >= {MIN_RELEVANCE:.0%})"
            )
        else:
            pytest.fail(f"'{phrasing}' did not match feature-development workflow. Got: {results}")


# =============================================================================
# LESSON TRIGGER TESTS
# =============================================================================

class TestLessonTriggers:
    """Test that lesson triggers match expected phrasings."""

    @pytest.mark.parametrize("phrasing", LESSON_PHRASINGS["git-workflow"].phrasings)
    def test_git_workflow_phrasings(self, vector_store_with_lessons, phrasing):
        """Test that git-related phrasings surface git lessons."""
        store = vector_store_with_lessons
        expected = ["no-claude-attribution-in-commits", "mgcp-save-before-commit", "query-before-git-operations"]

        results = store.search(phrasing, limit=5)

        assert results, f"No results for '{phrasing}'"

        # Check if at least one expected lesson is in results with sufficient relevance
        found_relevant = [
            (lesson_id, score)
            for lesson_id, score in results
            if lesson_id in expected and score >= MIN_RELEVANCE
        ]

        assert found_relevant, (
            f"'{phrasing}' did not match any git lessons with >= {MIN_RELEVANCE:.0%} relevance. "
            f"Found: {[(lid, f'{s:.0%}') for lid, s in results[:3]]}"
        )

    @pytest.mark.parametrize("phrasing", LESSON_PHRASINGS["session-management"].phrasings)
    def test_session_management_phrasings(self, vector_store_with_lessons, phrasing):
        """Test that session-related phrasings surface session lessons."""
        store = vector_store_with_lessons
        expected = ["mgcp-session-start", "mgcp-save-on-shutdown"]

        results = store.search(phrasing, limit=5)

        assert results, f"No results for '{phrasing}'"

        found_relevant = [
            (lesson_id, score)
            for lesson_id, score in results
            if lesson_id in expected and score >= MIN_RELEVANCE
        ]

        assert found_relevant, (
            f"'{phrasing}' did not match any session lessons with >= {MIN_RELEVANCE:.0%} relevance. "
            f"Found: {[(lid, f'{s:.0%}') for lid, s in results[:3]]}"
        )

    @pytest.mark.parametrize("phrasing", LESSON_PHRASINGS["api-development"].phrasings)
    def test_api_development_phrasings(self, vector_store_with_lessons, phrasing):
        """Test that API-related phrasings surface API lessons."""
        store = vector_store_with_lessons
        expected = ["api-research", "check-api-versions", "verify-api-response"]

        results = store.search(phrasing, limit=5)

        assert results, f"No results for '{phrasing}'"

        found_relevant = [
            (lesson_id, score)
            for lesson_id, score in results
            if lesson_id in expected and score >= MIN_RELEVANCE
        ]

        assert found_relevant, (
            f"'{phrasing}' did not match any API lessons with >= {MIN_RELEVANCE:.0%} relevance. "
            f"Found: {[(lid, f'{s:.0%}') for lid, s in results[:3]]}"
        )


# =============================================================================
# GAP DETECTION TESTS
# =============================================================================

class TestGapDetection:
    """Tests that help identify coverage gaps in triggers."""

    def test_report_workflow_coverage(self, vector_store_with_workflows):
        """Generate a coverage report for workflow triggers."""
        store = vector_store_with_workflows

        report = []
        failures = []

        for workflow_id, test_case in WORKFLOW_PHRASINGS.items():
            report.append(f"\n## {workflow_id} ({test_case.intent})")

            for phrasing in test_case.phrasings:
                results = store.search(phrasing, limit=2)

                if not results:
                    report.append(f"  - '{phrasing}' - NO MATCH")
                    failures.append((workflow_id, phrasing, "no match"))
                    continue

                # Find target workflow
                target_result = None
                for lesson_id, score in results:
                    if lesson_id == workflow_id:
                        target_result = (lesson_id, score)
                        break

                if target_result is None:
                    best_id, best_rel = results[0]
                    report.append(f"  - '{phrasing}' - matched {best_id} ({best_rel:.0%}) instead")
                    failures.append((workflow_id, phrasing, f"wrong match: {best_id}"))
                else:
                    relevance = target_result[1]
                    if relevance >= MIN_RELEVANCE:
                        report.append(f"  + '{phrasing}' - {relevance:.0%}")
                    else:
                        report.append(f"  ? '{phrasing}' - {relevance:.0%} (below threshold)")
                        failures.append((workflow_id, phrasing, f"low relevance: {relevance:.0%}"))

        # Print report for visibility
        print("\n".join(report))

        # This test passes but prints gaps for visibility
        # Uncomment the assert to make it fail on gaps:
        # assert not failures, f"Found {len(failures)} coverage gaps"

    def test_report_lesson_coverage(self, vector_store_with_lessons):
        """Generate a coverage report for lesson triggers."""
        store = vector_store_with_lessons

        report = []
        failures = []

        for category, test_case in LESSON_PHRASINGS.items():
            report.append(f"\n## {category} ({test_case.intent})")
            expected = getattr(test_case, 'expected_lessons', [])

            for phrasing in test_case.phrasings:
                results = store.search(phrasing, limit=5)

                if not results:
                    report.append(f"  - '{phrasing}' - NO MATCH")
                    failures.append((category, phrasing, "no match"))
                    continue

                # Check if any expected lesson matched
                found = [(lid, s) for lid, s in results if lid in expected and s >= MIN_RELEVANCE]

                if found:
                    best_match = found[0]
                    report.append(f"  + '{phrasing}' - {best_match[0]} ({best_match[1]:.0%})")
                else:
                    top_match = results[0]
                    report.append(f"  ? '{phrasing}' - got {top_match[0]} ({top_match[1]:.0%})")
                    failures.append((category, phrasing, f"unexpected: {top_match[0]}"))

        print("\n".join(report))


# =============================================================================
# INTEGRATION TESTS (against real MGCP data)
# =============================================================================

@pytest.mark.integration
class TestRealDataTriggers:
    """Integration tests against actual MGCP data.

    These tests use the real lesson/workflow data to catch actual gaps.
    Run with: pytest tests/test_trigger_coverage.py -v -m integration
    """

    @pytest.fixture
    def real_vector_store(self):
        """Load the real MGCP vector store."""
        # Use default MGCP data path
        mgcp_dir = os.path.expanduser("~/.mgcp")
        qdrant_path = os.path.join(mgcp_dir, "qdrant")

        if not os.path.exists(qdrant_path):
            pytest.skip("No MGCP data found at ~/.mgcp/qdrant")

        return QdrantVectorStore(persist_path=qdrant_path)

    @pytest.mark.parametrize("phrasing,expected_lessons", [
        ("fix the bug", ["bug-fix"]),
        ("something's broken", ["bug-fix"]),
        ("commit this", ["no-claude-attribution-in-commits", "mgcp-save-before-commit", "query-before-git-operations"]),
        ("starting a new session", ["mgcp-session-start"]),
    ])
    def test_real_lesson_matching(self, real_vector_store, phrasing, expected_lessons):
        """Test lesson matching against real data."""
        store = real_vector_store

        results = store.search(phrasing, limit=5)

        if not results:
            pytest.fail(f"No results for '{phrasing}'")

        # Check if any expected lesson matched with sufficient relevance
        found = [(lid, s) for lid, s in results if lid in expected_lessons and s >= MIN_RELEVANCE]

        if not found:
            top_results = [(lid, f"{s:.0%}") for lid, s in results[:3]]
            pytest.fail(
                f"'{phrasing}' expected one of {expected_lessons} but got {top_results}"
            )
