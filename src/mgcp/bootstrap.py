"""Bootstrap lessons to seed the MGCP database with initial knowledge."""

import asyncio

from .models import Example, Lesson, Relationship, Workflow, WorkflowStep, WorkflowStepLesson
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
        id="security",
        trigger="security, secure, vulnerability, attack, exploit, injection, xss, csrf",
        action="Apply security best practices at every step of development",
        rationale="Security vulnerabilities are expensive to fix after deployment. Build security in from the start.",
        tags=["meta", "security", "quality"],
    ),
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

    # SECURITY CHILDREN
    Lesson(
        id="validate-input",
        trigger="input, user input, form, request, parameter, query",
        action="Validate and sanitize all external input before use",
        rationale="Untrusted input is the root cause of injection attacks, XSS, and many vulnerabilities",
        parent_id="security",
        tags=["security", "input-validation"],
        examples=[
            Example(
                label="bad",
                code="query = f\"SELECT * FROM users WHERE id = {user_input}\"",
                explanation="SQL injection vulnerability - user controls query",
            ),
            Example(
                label="good",
                code="query = \"SELECT * FROM users WHERE id = ?\"\ncursor.execute(query, (user_input,))",
                explanation="Parameterized query prevents injection",
            ),
        ],
    ),
    Lesson(
        id="no-hardcoded-secrets",
        trigger="password, secret, key, token, credential, api key",
        action="Never hardcode secrets - use environment variables or secret managers",
        rationale="Hardcoded secrets end up in version control and are easily leaked",
        parent_id="security",
        tags=["security", "secrets"],
        examples=[
            Example(
                label="bad",
                code="API_KEY = \"sk-abc123secret\"  # Committed to git",
                explanation="Secret will be in git history forever",
            ),
            Example(
                label="good",
                code="API_KEY = os.environ.get('API_KEY')\nif not API_KEY:\n    raise ValueError('API_KEY not set')",
                explanation="Secret comes from environment, not code",
            ),
        ],
    ),
    Lesson(
        id="least-privilege",
        trigger="permission, access, role, privilege, scope, capability",
        action="Request only the minimum permissions needed for the task",
        rationale="Excessive permissions increase attack surface and blast radius",
        parent_id="security",
        tags=["security", "permissions"],
        examples=[
            Example(
                label="bad",
                code="# Request admin access when read-only would suffice\nconn = db.connect(role='admin')",
                explanation="Over-privileged connection",
            ),
            Example(
                label="good",
                code="# Use read-only connection for queries\nconn = db.connect(role='readonly')",
                explanation="Minimum privilege for the task",
            ),
        ],
    ),
    Lesson(
        id="secure-error-messages",
        trigger="error message, exception, stack trace, debug",
        action="Never expose sensitive information in error messages to users",
        rationale="Detailed errors help attackers understand system internals",
        parent_id="security",
        tags=["security", "errors"],
        examples=[
            Example(
                label="bad",
                code="return f'Database error: {e}'  # Exposes DB details",
                explanation="Reveals database structure to attacker",
            ),
            Example(
                label="good",
                code="logger.error(f'DB error: {e}')  # Log internally\nreturn 'An error occurred'  # Generic to user",
                explanation="Log details internally, show generic message to user",
            ),
        ],
    ),
    Lesson(
        id="dependency-security",
        trigger="dependency, package, library, npm, pip, vulnerability, CVE",
        action="Audit dependencies for known vulnerabilities before adding them",
        rationale="Supply chain attacks through compromised dependencies are common",
        parent_id="security",
        tags=["security", "dependencies"],
        examples=[
            Example(
                label="good",
                code="# Before adding: pip-audit, npm audit, or snyk\n# Check: is it maintained? recent commits? known issues?",
                explanation="Audit before trusting third-party code",
            ),
        ],
    ),
    Lesson(
        id="escape-output",
        trigger="output, html, template, render, display, XSS",
        action="Escape output based on context (HTML, JS, URL, etc.)",
        rationale="Unescaped output enables XSS attacks",
        parent_id="security",
        tags=["security", "xss", "output"],
        examples=[
            Example(
                label="bad",
                code="return f'<div>{user_name}</div>'  # XSS if name contains <script>",
                explanation="User-controlled content rendered as HTML",
            ),
            Example(
                label="good",
                code="from html import escape\nreturn f'<div>{escape(user_name)}</div>'",
                explanation="HTML entities escaped, script tags neutralized",
            ),
        ],
    ),

    # =========================================================================
    # MGCP SELF-TEACHING LESSONS
    # These lessons teach how to use MGCP itself effectively
    # =========================================================================
    Lesson(
        id="mgcp-usage",
        trigger="mgcp, memory, lessons, context, catalogue, save context",
        action="Use MGCP tools throughout the session to capture knowledge, not just at start/end",
        rationale="MGCP is most valuable when knowledge is captured as it's discovered, not reconstructed later",
        tags=["meta", "mgcp", "workflow"],
    ),
    Lesson(
        id="mgcp-save-before-commit",
        trigger="commit, git commit, push, let's commit, commit this, ready to commit",
        action="BEFORE committing, call save_project_context with notes summarizing what was accomplished, active_files listing key files changed, and decision for any architectural choices made.",
        rationale="Project context captures the 'why' behind changes that git commits don't preserve. Saving before commit ensures continuity between sessions.",
        parent_id="mgcp-usage",
        tags=["mgcp", "workflow", "git", "session-management"],
    ),
    Lesson(
        id="mgcp-save-on-shutdown",
        trigger="shutdown, end session, done for now, signing off, closing, goodbye, that's all",
        action="Call save_project_context before session ends. Include notes about current state, any blockers, and what to pick up next time.",
        rationale="Session context is lost when the session closes. Saving ensures the next session can resume seamlessly without re-explaining context.",
        parent_id="mgcp-usage",
        tags=["mgcp", "workflow", "session-management"],
    ),
    Lesson(
        id="mgcp-record-decisions",
        trigger="decided to, chose, picked, went with, selected, decision, chose X over Y, why did we",
        action="When making an architectural or design decision, call add_catalogue_decision with title, decision, rationale, and alternatives considered. This prevents re-litigating the same decisions later.",
        rationale="Decisions without recorded rationale get questioned repeatedly. Recording alternatives considered shows the decision was thoughtful.",
        parent_id="mgcp-usage",
        tags=["mgcp", "architecture", "decisions", "documentation"],
    ),
    Lesson(
        id="mgcp-record-couplings",
        trigger="these files, change together, coupled, related files, when you modify, also update, depends on",
        action="When discovering files that must change together, call add_catalogue_coupling with the files list and reason. This helps future sessions know what else to check when modifying code.",
        rationale="File couplings are tribal knowledge that gets lost. Recording them prevents bugs from partial updates and helps onboarding.",
        parent_id="mgcp-usage",
        tags=["mgcp", "architecture", "couplings", "maintenance"],
    ),
    Lesson(
        id="mgcp-record-gotchas",
        trigger="gotcha, watch out, careful, quirk, weird, surprising, unexpected, don't forget, remember to",
        action="When discovering a gotcha or non-obvious behavior, call add_catalogue_arch_note with title, description, and category (gotcha/architecture/convention/performance). These save future debugging time.",
        rationale="Gotchas are discovered through pain and forgotten quickly. Recording them immediately prevents others from hitting the same issues.",
        parent_id="mgcp-usage",
        tags=["mgcp", "architecture", "gotchas", "documentation"],
    ),
    Lesson(
        id="mgcp-add-reusable-lessons",
        trigger="learned, discovered, realized, figured out, turns out, the trick is, pro tip, best practice",
        action="When learning something applicable beyond this specific project, call add_lesson with a clear trigger (when it applies), action (what to do), and rationale (why). Good lessons are actionable imperatives.",
        rationale="Lessons are the core value of MGCP - reusable knowledge across all sessions. If you learned it once, you shouldn't have to learn it again.",
        parent_id="mgcp-usage",
        tags=["mgcp", "meta", "lessons", "knowledge-management"],
    ),
]


# =============================================================================
# DEFAULT WORKFLOW
# A comprehensive development workflow with lessons linked to each step
# =============================================================================

FEATURE_DEVELOPMENT_WORKFLOW = Workflow(
    id="feature-development",
    name="Feature Development",
    description="Use when implementing a new feature or significant change. Ensures research, planning, documentation, and testing.",
    trigger="new feature, implement, add functionality, build, create",
    tags=["development", "feature", "standard"],
    steps=[
        WorkflowStep(
            id="research",
            name="Research",
            description="Understand the task, existing code, and relevant APIs/libraries before writing any code.",
            order=1,
            guidance="Read existing code first. Check API documentation for current versions. Search for similar patterns in the codebase.",
            checklist=[
                "Read relevant existing code",
                "Check API/library documentation",
                "Verify versions of dependencies",
                "Identify files that will be modified",
                "Understand edge cases",
            ],
            outputs=["understanding of scope", "list of files to modify", "identified risks"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="api-research",
                    relevance="Must research APIs before using them to avoid outdated patterns and understand current behavior",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="check-api-versions",
                    relevance="Version mismatches cause subtle bugs. Always verify you're reading docs for the right version",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="verify-api-response",
                    relevance="Log actual responses during research to understand real API behavior",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="dependency-security",
                    relevance="Check dependencies for known vulnerabilities during research phase",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="plan",
            name="Plan",
            description="Design the implementation approach before coding. Identify potential issues and dependencies.",
            order=2,
            guidance="Break down into small steps. Identify which files change together. Consider rollback strategy.",
            checklist=[
                "Break task into implementable steps",
                "Identify file dependencies/couplings",
                "Plan error handling approach",
                "Consider edge cases in design",
                "Plan test coverage",
            ],
            outputs=["step-by-step plan", "file change list", "risk mitigation"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="verification",
                    relevance="Plan what needs verification at each step to catch errors early",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="error-handling",
                    relevance="Design error handling into the plan, not as an afterthought",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="document",
            name="Document Plan",
            description="Record the plan with sources. This prevents re-research and enables review.",
            order=3,
            guidance="Write down what you learned in research. Cite API docs and version numbers. This helps future debugging.",
            checklist=[
                "Document approach in code comments or project docs",
                "Record API versions and doc links",
                "Note any assumptions being made",
                "Record decisions and rationale",
            ],
            outputs=["documented plan", "cited sources", "recorded assumptions"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="error-context",
                    relevance="Document context now so error messages and debugging later have full information",
                    priority=3,
                ),
            ],
        ),
        WorkflowStep(
            id="execute",
            name="Execute",
            description="Implement step-by-step, testing as you go. Don't write everything then test at the end.",
            order=4,
            guidance="Implement one piece, test it, commit. Don't batch large changes. Debug immediately when issues arise.",
            checklist=[
                "Implement incrementally",
                "Test each piece before moving on",
                "Commit working increments",
                "Handle errors as they arise",
                "Verify assumptions in code",
            ],
            outputs=["working implementation", "incremental commits"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="api-research",
                    relevance="Re-verify API behavior during implementation - documentation may not match reality",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="verify-api-response",
                    relevance="Log responses during implementation to debug issues immediately",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="testing",
                    relevance="Test with known inputs as you implement, not just at the end",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="verify-before-assert",
                    relevance="Add verification checks in code for assumptions from research phase",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="specific-exceptions",
                    relevance="Catch specific exceptions to enable debugging, not bare except",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="error-context",
                    relevance="Include full context in error messages for future debugging",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="validate-input",
                    relevance="Validate all external input - user data, API responses, file contents",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="no-hardcoded-secrets",
                    relevance="Never commit secrets to code - use environment variables",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="escape-output",
                    relevance="Escape output when rendering to HTML, SQL, or other contexts",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="secure-error-messages",
                    relevance="Don't leak sensitive info in error messages shown to users",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="test",
            name="Test",
            description="Comprehensive testing including edge cases. Verify the feature works as intended.",
            order=5,
            guidance="Test happy path, error cases, and edge cases. Test integration points. Verify error messages are helpful.",
            checklist=[
                "Happy path works",
                "Error cases handled gracefully",
                "Edge cases covered (empty, null, boundary)",
                "Error messages are informative",
                "No regressions in existing functionality",
            ],
            outputs=["passing tests", "verified behavior"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="testing",
                    relevance="Test with known inputs to establish baseline behavior",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="test-known-inputs",
                    relevance="Known input/output pairs make it obvious when behavior changes",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="test-edge-cases",
                    relevance="Edge cases cause most production bugs - test empty, null, zero, max",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="verification",
                    relevance="Verify all assumptions from planning phase are validated by tests",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="review",
            name="Review",
            description="Self-review before commit. Check for obvious issues, code quality, and documentation.",
            order=6,
            guidance="Review diff before committing. Look for debug code, TODOs, and incomplete error handling.",
            checklist=[
                "Review diff for obvious issues",
                "Remove debug code and print statements",
                "Verify error handling is complete",
                "Check documentation is updated",
                "Ensure tests are committed",
                "Check for hardcoded secrets (grep for api_key, token, password, secret)",
                "Verify .env files are gitignored",
            ],
            outputs=["clean commit", "complete feature"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="verification",
                    relevance="Final verification that all assumptions from research were validated",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="error-handling",
                    relevance="Verify error handling is complete and consistent before commit",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="no-hardcoded-secrets",
                    relevance="Critical: Check for accidentally committed secrets before pushing",
                    priority=1,
                ),
            ],
        ),
    ],
)

BUG_FIX_WORKFLOW = Workflow(
    id="bug-fix",
    name="Bug Fix",
    description="Use when fixing a bug. Ensures understanding the root cause before applying a fix.",
    trigger="bug, fix, issue, broken, not working, error, crash",
    tags=["development", "bugfix", "debugging"],
    steps=[
        WorkflowStep(
            id="reproduce",
            name="Reproduce",
            description="Reliably reproduce the bug before attempting to fix it.",
            order=1,
            guidance="Create a minimal reproduction case. Document exact steps to trigger the bug.",
            checklist=[
                "Can reproduce the bug consistently",
                "Documented reproduction steps",
                "Identified exact error message/behavior",
            ],
            outputs=["reproduction steps", "error details"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="verification",
                    relevance="Verify you can reproduce before assuming you understand the bug",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="test-edge-cases",
                    relevance="Check if bug occurs at boundaries/edge cases",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="investigate",
            name="Investigate",
            description="Find the root cause. Don't just fix the symptom.",
            order=2,
            guidance="Use logging, debugger, or print statements to trace execution. Find where behavior diverges from expected.",
            checklist=[
                "Identified root cause (not just symptom)",
                "Understood why the bug occurs",
                "Checked for similar issues elsewhere",
            ],
            outputs=["root cause identified", "understanding of failure mode"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="verify-api-response",
                    relevance="Log actual values to see where they diverge from expected",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="error-context",
                    relevance="Add context to understand the full picture of what's happening",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="fix",
            name="Fix",
            description="Apply the fix. Keep it minimal - don't refactor while fixing.",
            order=3,
            guidance="Fix only the bug. Don't add features or refactor. That's scope creep that introduces new bugs.",
            checklist=[
                "Fix addresses root cause",
                "Fix is minimal (no scope creep)",
                "Added defensive checks if appropriate",
            ],
            outputs=["fix applied", "minimal diff"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="specific-exceptions",
                    relevance="Add specific exception handling for the failure mode",
                    priority=2,
                ),
                WorkflowStepLesson(
                    lesson_id="verify-before-assert",
                    relevance="Add verification for the condition that caused the bug",
                    priority=2,
                ),
            ],
        ),
        WorkflowStep(
            id="verify",
            name="Verify",
            description="Confirm the fix works and doesn't break anything else.",
            order=4,
            guidance="Test the fix with the original reproduction case. Run full test suite for regressions.",
            checklist=[
                "Original bug no longer reproduces",
                "Added test to prevent regression",
                "Existing tests still pass",
                "No new issues introduced",
            ],
            outputs=["verified fix", "regression test"],
            lessons=[
                WorkflowStepLesson(
                    lesson_id="testing",
                    relevance="Test the fix with the reproduction case from step 1",
                    priority=1,
                ),
                WorkflowStepLesson(
                    lesson_id="test-known-inputs",
                    relevance="Add regression test with the input that triggered the bug",
                    priority=1,
                ),
            ],
        ),
    ],
)

DEFAULT_WORKFLOWS = [FEATURE_DEVELOPMENT_WORKFLOW, BUG_FIX_WORKFLOW]


# =============================================================================
# LESSON RELATIONSHIPS
# Cross-links between lessons to demonstrate graph connectivity
# =============================================================================

LESSON_RELATIONSHIPS = [
    # Prerequisite relationships (A must be understood before B)
    ("api-research", "check-api-versions", "prerequisite", "Must understand research process before checking versions"),
    ("api-research", "verify-api-response", "prerequisite", "Research APIs before logging responses"),
    ("testing", "test-known-inputs", "prerequisite", "Understand testing principles before specific patterns"),
    ("testing", "test-edge-cases", "prerequisite", "General testing before edge case specifics"),
    ("error-handling", "specific-exceptions", "prerequisite", "Understand error handling before exception specifics"),
    ("error-handling", "error-context", "prerequisite", "General handling before context patterns"),

    # Complements relationships (A and B work together)
    ("check-api-versions", "verify-api-response", "complements", "Version checking and response logging work together"),
    ("test-known-inputs", "test-edge-cases", "complements", "Known inputs and edge cases are complementary tests"),
    ("specific-exceptions", "error-context", "complements", "Specific exceptions with contextual messages"),
    ("verify-before-assert", "error-context", "complements", "Verification with informative error messages"),

    # Related relationships (A and B cover similar topics)
    ("verification", "testing", "related", "Both concern validating correctness"),
    ("verification", "api-research", "related", "Both concern verifying before acting"),
    ("testing", "error-handling", "related", "Testing often reveals error handling needs"),
    ("check-api-versions", "check-breaking-changes", "related", "Both concern dependency management"),
    ("verify-file-paths", "verify-calculations", "related", "Both are verification patterns"),

    # Sequence relationships (After A, often do B)
    ("api-research", "testing", "sequence_next", "After research, test your understanding"),
    ("test-known-inputs", "verify-before-assert", "sequence_next", "After testing, add runtime verification"),
    ("specific-exceptions", "test-edge-cases", "sequence_next", "After exception handling, test edge cases"),

    # Alternative relationships (A or B can solve similar problems)
    ("verify-before-assert", "verify-calculations", "alternative", "Different verification approaches"),

    # Security relationships
    ("security", "validate-input", "prerequisite", "Understand security principles before input validation"),
    ("security", "no-hardcoded-secrets", "prerequisite", "Understand security before secrets management"),
    ("security", "least-privilege", "prerequisite", "Understand security before privilege management"),
    ("security", "escape-output", "prerequisite", "Understand security before output handling"),
    ("validate-input", "escape-output", "complements", "Input validation and output escaping work together"),
    ("secure-error-messages", "error-context", "related", "Both deal with error information, but different audiences"),
    ("dependency-security", "check-api-versions", "related", "Both concern dependency management and versions"),
    ("validate-input", "test-edge-cases", "complements", "Test edge cases to verify input validation"),
    ("no-hardcoded-secrets", "verify-file-paths", "related", "Both concern sensitive resource access"),

    # MGCP self-teaching relationships
    ("mgcp-usage", "mgcp-save-before-commit", "prerequisite", "Understand MGCP usage before specific triggers"),
    ("mgcp-usage", "mgcp-save-on-shutdown", "prerequisite", "Understand MGCP usage before specific triggers"),
    ("mgcp-usage", "mgcp-record-decisions", "prerequisite", "Understand MGCP usage before catalogue tools"),
    ("mgcp-usage", "mgcp-record-couplings", "prerequisite", "Understand MGCP usage before catalogue tools"),
    ("mgcp-usage", "mgcp-record-gotchas", "prerequisite", "Understand MGCP usage before catalogue tools"),
    ("mgcp-usage", "mgcp-add-reusable-lessons", "prerequisite", "Understand MGCP usage before adding lessons"),
    ("mgcp-save-before-commit", "mgcp-save-on-shutdown", "related", "Both concern saving context at key moments"),
    ("mgcp-record-decisions", "mgcp-record-gotchas", "complements", "Decisions and gotchas both capture architectural knowledge"),
    ("mgcp-record-couplings", "mgcp-record-gotchas", "complements", "Couplings and gotchas both capture maintenance knowledge"),
    ("mgcp-add-reusable-lessons", "mgcp-save-before-commit", "sequence_next", "After adding lessons, save context before committing"),
]


async def seed_database() -> None:
    """Seed the database with bootstrap lessons and workflows."""
    store = LessonStore()
    vector_store = VectorStore()
    graph = LessonGraph()

    # Seed lessons
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

    print(f"\nLessons: {added} added, {skipped} skipped")
    print(f"Total lessons in database: {len(await store.get_all_lessons())}")

    # Seed workflows
    print("\nSeeding database with default workflows...")

    wf_added = 0
    wf_skipped = 0

    for workflow in DEFAULT_WORKFLOWS:
        existing = await store.get_workflow(workflow.id)
        if existing:
            print(f"  Skipping workflow {workflow.id} (already exists)")
            wf_skipped += 1
            continue

        await store.save_workflow(workflow)
        print(f"  Added workflow {workflow.id} ({len(workflow.steps)} steps)")
        wf_added += 1

    print(f"\nWorkflows: {wf_added} added, {wf_skipped} skipped")
    print(f"Total workflows in database: {len(await store.get_all_workflows())}")

    # Seed lesson relationships
    print("\nSeeding lesson relationships...")

    rel_added = 0
    rel_skipped = 0

    for source_id, target_id, rel_type, context in LESSON_RELATIONSHIPS:
        source = await store.get_lesson(source_id)
        target = await store.get_lesson(target_id)

        if not source or not target:
            print(f"  Skipping {source_id} -> {target_id} (lesson not found)")
            rel_skipped += 1
            continue

        # Check if relationship already exists
        existing = [r for r in source.relationships if r.target == target_id and r.type == rel_type]
        if existing:
            rel_skipped += 1
            continue

        # Add relationship to source lesson
        new_rel = Relationship(
            target=target_id,
            type=rel_type,
            weight=0.7,
            context=[context],
            bidirectional=True,
        )
        source.relationships.append(new_rel)
        if target_id not in source.related_ids:
            source.related_ids.append(target_id)
        await store.update_lesson(source)

        # Add reverse relationship to target lesson
        reverse_type = rel_type
        if rel_type == "prerequisite":
            reverse_type = "sequence_next"
        elif rel_type == "sequence_next":
            reverse_type = "prerequisite"

        reverse_rel = Relationship(
            target=source_id,
            type=reverse_type,
            weight=0.7,
            context=[context],
            bidirectional=True,
        )
        if source_id not in [r.target for r in target.relationships]:
            target.relationships.append(reverse_rel)
            if source_id not in target.related_ids:
                target.related_ids.append(source_id)
            await store.update_lesson(target)

        # Update graph
        graph.add_lesson(source)
        graph.add_lesson(target)

        print(f"  Added {source_id} --[{rel_type}]--> {target_id}")
        rel_added += 1

    print(f"\nRelationships: {rel_added} added, {rel_skipped} skipped")


def main():
    """Run bootstrap seeding."""
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print("""MGCP Bootstrap - Seed database with initial lessons

Usage: mgcp-bootstrap [OPTIONS]

Seeds the MGCP database with foundational lessons covering common
development practices like verification, error handling, and testing.

Options:
  -h, --help     Show this help message
  -V, --version  Show version number

The bootstrap lessons are safe to run multiple times - existing lessons
will be skipped. Data is stored in ~/.mgcp/ by default.
""")
            return
        elif sys.argv[1] in ("--version", "-V"):
            print("mgcp-bootstrap 1.0.0")
            return

    asyncio.run(seed_database())


if __name__ == "__main__":
    main()