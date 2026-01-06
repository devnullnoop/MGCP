"""Bootstrap lessons to seed the MGCP database with initial knowledge."""

import asyncio

from .models import Example, Lesson
from .persistence import LessonStore
from .vector_store import VectorStore
from .graph import LessonGraph


# =============================================================================
# BOOTSTRAP LESSONS
# Based on common developer pitfalls and best practices
# =============================================================================

BOOTSTRAP_LESSONS = [
    # ROOT CATEGORIES
    Lesson(
        id="verification",
        trigger="verify, check, validate, confirm, ensure, test",
        action="Always verify assumptions before acting on them",
        rationale="Many bugs come from acting on assumptions that turn out to be false",
        tags=["meta", "verification", "quality"],
        examples=[
            Example(
                label="bad",
                code="# Assume the API returns JSON\ndata = response.json()",
                explanation="Will crash if API returns HTML error page",
            ),
            Example(
                label="good",
                code="if response.headers.get('content-type') == 'application/json':\n    data = response.json()",
                explanation="Check content type before parsing",
            ),
        ],
    ),
    Lesson(
        id="api-research",
        trigger="API, library, package, dependency, import, install",
        action="Research APIs and libraries before using them",
        rationale="Documentation, versions, and behavior change. Verify current state.",
        tags=["meta", "research", "apis"],
    ),
    Lesson(
        id="testing",
        trigger="test, debug, verify, check, validate",
        action="Test with known inputs before integrating",
        rationale="Catch errors early when the problem space is small",
        tags=["meta", "testing", "quality"],
    ),
    Lesson(
        id="error-handling",
        trigger="error, exception, fail, crash, bug, handle",
        action="Handle errors gracefully with informative messages",
        rationale="Good error handling aids debugging and user experience",
        tags=["meta", "errors", "quality"],
    ),

    # VERIFICATION CHILDREN
    Lesson(
        id="verify-before-assert",
        trigger="assert, assumption, expect, should be",
        action="Verify conditions before asserting them in code",
        rationale="Assertions that fail in production cause crashes. Verify first, then assert.",
        parent_id="verification",
        tags=["verification", "assertions"],
        examples=[
            Example(
                label="bad",
                code="assert user is not None  # Will crash in production",
                explanation="Assertions can be disabled; crashes aren't graceful",
            ),
            Example(
                label="good",
                code="if user is None:\n    raise ValueError('User required')",
                explanation="Explicit check with informative error",
            ),
        ],
    ),
    Lesson(
        id="verify-calculations",
        trigger="calculate, formula, math, estimate, compute",
        action="Sanity-check calculation results against expected magnitudes",
        rationale="Formulas that look correct can produce wildly wrong results",
        parent_id="verification",
        tags=["verification", "math"],
        examples=[
            Example(
                label="bad",
                code="memory_needed = nodes * avg_size * 1000  # Looks reasonable",
                explanation="Magic numbers and unverified formulas",
            ),
            Example(
                label="good",
                code="memory_needed = nodes * avg_size * 1000\nassert 1_000_000 < memory_needed < 1_000_000_000, f'Unexpected: {memory_needed}'",
                explanation="Sanity check against expected range",
            ),
        ],
    ),
    Lesson(
        id="verify-file-paths",
        trigger="file, path, directory, folder, read, write, open",
        action="Verify file paths exist before operations",
        rationale="File operations silently fail or crash without path verification",
        parent_id="verification",
        tags=["verification", "files"],
    ),

    # API RESEARCH CHILDREN
    Lesson(
        id="check-api-versions",
        trigger="version, upgrade, update, latest, deprecated",
        action="Check current API/library versions before using examples",
        rationale="Online examples may be outdated. APIs change between versions.",
        parent_id="api-research",
        tags=["apis", "versions"],
        examples=[
            Example(
                label="bad",
                code="# Copy-pasted from 2019 Stack Overflow\nrequests.get(url, verify=False)",
                explanation="Old patterns may be insecure or deprecated",
            ),
            Example(
                label="good",
                code="# First: pip show requests -> version\n# Then: check requests docs for current best practice",
                explanation="Verify version and check current docs",
            ),
        ],
    ),
    Lesson(
        id="check-breaking-changes",
        trigger="breaking, migration, upgrade, changelog",
        action="Read changelogs before upgrading dependencies",
        rationale="Minor version bumps can contain breaking changes",
        parent_id="api-research",
        tags=["apis", "dependencies"],
    ),
    Lesson(
        id="verify-api-response",
        trigger="response, API, request, fetch, call",
        action="Log and inspect actual API responses before parsing",
        rationale="APIs don't always return what documentation says",
        parent_id="api-research",
        tags=["apis", "debugging"],
    ),

    # TESTING CHILDREN
    Lesson(
        id="test-known-inputs",
        trigger="test, unit test, validate, verify output",
        action="Test functions with known input/output pairs first",
        rationale="Known pairs make it obvious when behavior changes",
        parent_id="testing",
        tags=["testing", "unit-tests"],
        examples=[
            Example(
                label="good",
                code="def test_parse_date():\n    assert parse_date('2024-01-15') == date(2024, 1, 15)",
                explanation="Known input, known output, easy to verify",
            ),
        ],
    ),
    Lesson(
        id="test-edge-cases",
        trigger="edge case, boundary, empty, null, zero, max",
        action="Always test empty, null, zero, and boundary values",
        rationale="Edge cases cause most production bugs",
        parent_id="testing",
        tags=["testing", "edge-cases"],
        examples=[
            Example(
                label="good",
                code="def test_process_list():\n    assert process([]) == []  # empty\n    assert process([1]) == [1]  # single\n    assert process(None) raises ValueError",
                explanation="Empty, single, and null cases covered",
            ),
        ],
    ),

    # ERROR HANDLING CHILDREN
    Lesson(
        id="specific-exceptions",
        trigger="except, catch, exception, error handling",
        action="Catch specific exceptions, not bare except",
        rationale="Bare except hides bugs and catches KeyboardInterrupt",
        parent_id="error-handling",
        tags=["errors", "exceptions"],
        examples=[
            Example(
                label="bad",
                code="try:\n    risky()\nexcept:\n    pass",
                explanation="Catches everything, hides real errors",
            ),
            Example(
                label="good",
                code="try:\n    risky()\nexcept ValueError as e:\n    logger.error(f'Invalid value: {e}')",
                explanation="Specific exception, logged with context",
            ),
        ],
    ),
    Lesson(
        id="error-context",
        trigger="error message, exception message, logging error",
        action="Include context in error messages (what, where, why)",
        rationale="'Error occurred' is useless. Context enables debugging.",
        parent_id="error-handling",
        tags=["errors", "debugging"],
        examples=[
            Example(
                label="bad",
                code="raise ValueError('Invalid input')",
                explanation="No context about what input or why invalid",
            ),
            Example(
                label="good",
                code="raise ValueError(f'User ID {user_id} not found in database {db_name}')",
                explanation="What failed, which value, where to look",
            ),
        ],
    ),
]


async def seed_database() -> None:
    """Seed the database with bootstrap lessons."""
    store = LessonStore()
    vector_store = VectorStore()
    graph = LessonGraph()

    print("Seeding database with bootstrap lessons...")

    added = 0
    skipped = 0

    for lesson in BOOTSTRAP_LESSONS:
        existing = await store.get_lesson(lesson.id)
        if existing:
            print(f"  Skipping {lesson.id} (already exists)")
            skipped += 1
            continue

        await store.add_lesson(lesson)
        vector_store.add_lesson(lesson)
        graph.add_lesson(lesson)
        print(f"  Added {lesson.id}")
        added += 1

    print(f"\nDone: {added} added, {skipped} skipped")
    print(f"Total lessons in database: {len(await store.get_all_lessons())}")


def main():
    """Run bootstrap seeding."""
    asyncio.run(seed_database())


if __name__ == "__main__":
    main()