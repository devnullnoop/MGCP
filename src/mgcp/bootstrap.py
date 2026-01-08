"""Bootstrap lessons to seed the MGCP database with initial knowledge."""

import asyncio

from .graph import LessonGraph
from .models import Example, Lesson, Relationship, Workflow, WorkflowStep, WorkflowStepLesson
from .persistence import LessonStore
from .vector_store import VectorStore

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

    # MGCP Knowledge Storage Types - Critical for correct usage
    Lesson(
        id="mgcp-knowledge-storage-types",
        trigger="mgcp, store knowledge, save lesson, remember this, add to memory, what to save, where to store",
        action="MGCP has 3 storage mechanisms - choose correctly: (1) LESSONS = generic, cross-project knowledge, (2) CATALOGUE = project-specific facts, (3) WORKFLOW LINKS = attach lessons to process steps. Ask 'Is this universal or project-specific?' before storing.",
        rationale="Using the wrong storage pollutes the knowledge graph. Generic lessons with project details become noise. Project facts in lessons clutter unrelated projects.",
        parent_id="mgcp-usage",
        tags=["mgcp", "meta", "knowledge-management"],
    ),
    Lesson(
        id="lessons-are-generic-knowledge",
        trigger="add_lesson, create lesson, new lesson, learned something",
        action="Before calling add_lesson, ask: 'Would this apply to ANY project?' If yes, make it abstract and reusable. If it's project-specific, use the catalogue instead (add_catalogue_arch_note, add_catalogue_decision, add_catalogue_convention, etc.).",
        rationale="Lessons polluted with project-specific details become noise in other projects. Keep lessons abstract: 'verify API responses' not 'verify the Stripe API response in payment.py'.",
        parent_id="mgcp-knowledge-storage-types",
        tags=["mgcp", "lessons", "knowledge-management"],
        examples=[
            Example(
                label="bad",
                code="add_lesson(id='stripe-api-check', trigger='payment', action='Check Stripe API v3 in payment.py')",
                explanation="Too specific - mentions Stripe, v3, and payment.py which are project details",
            ),
            Example(
                label="good",
                code="add_lesson(id='verify-api-responses', trigger='API, response', action='Verify API responses match expected schema before parsing')",
                explanation="Generic - applies to any API in any project",
            ),
        ],
    ),
    Lesson(
        id="catalogue-for-project-specific",
        trigger="project-specific, this project, this codebase, architecture decision, file coupling, convention, gotcha",
        action="Use the project catalogue for project-specific knowledge: add_catalogue_arch_note (patterns/gotchas), add_catalogue_decision (choices with rationale), add_catalogue_convention (local rules), add_catalogue_coupling (linked files), add_catalogue_security_note (vulnerabilities). NOT lessons.",
        rationale="The catalogue is scoped to a project_path. It won't pollute other projects. Lessons are global and should only contain universally applicable knowledge.",
        parent_id="mgcp-knowledge-storage-types",
        tags=["mgcp", "catalogue", "knowledge-management"],
        examples=[
            Example(
                label="bad",
                code="add_lesson(id='our-auth-pattern', action='Use JWT with Redis sessions')",
                explanation="Project-specific architecture detail stored as global lesson",
            ),
            Example(
                label="good",
                code="add_catalogue_decision(title='Auth approach', decision='JWT with Redis', rationale='Needed stateless + session revocation')",
                explanation="Project decision stored in catalogue, won't appear in other projects",
            ),
        ],
    ),
    Lesson(
        id="workflow-links-for-process-guidance",
        trigger="workflow, process, step-by-step, checklist, review workflow",
        action="To add guidance to a workflow step, use link_lesson_to_workflow_step - don't create new lessons just for workflows. Workflows aggregate existing lessons at the right moments. Check get_workflow first to see what lessons are already linked.",
        rationale="Workflows are process templates. They don't contain knowledge themselves - they reference lessons that apply at each step. This keeps knowledge DRY and allows lessons to be reused across multiple workflows.",
        parent_id="mgcp-knowledge-storage-types",
        tags=["mgcp", "workflows", "knowledge-management"],
    ),

    # =========================================================================
    # MGCP SESSION LIFECYCLE - Critical for bidirectional communication
    # =========================================================================
    Lesson(
        id="mgcp-session-start",
        trigger="session start, new session, starting, beginning, hello, hi, let's begin, help me with",
        action="At SESSION START, ALWAYS do two things: (1) Call get_project_context with the project path to load todos, decisions, and prior state. (2) Call query_lessons with a brief description of the task to surface relevant knowledge. Do these BEFORE starting any work.",
        rationale="Without loading context, you start from zero every session. Without querying lessons, you'll repeat past mistakes. These two calls bootstrap your knowledge for the session.",
        parent_id="mgcp-usage",
        tags=["mgcp", "session", "startup", "critical"],
        examples=[
            Example(
                label="good",
                code="# User says: 'Help me add authentication'\n# Step 1: get_project_context(project_path='/path/to/project')\n# Step 2: query_lessons(task_description='implementing authentication')\n# Step 3: Now start working with full context",
                explanation="Load context and query lessons BEFORE writing any code",
            ),
        ],
    ),
    Lesson(
        id="mgcp-query-before-action",
        trigger="before, about to, going to, let me, I'll, implement, fix, debug, refactor, add, create, modify",
        action="BEFORE taking significant action (implementing, debugging, refactoring), call query_lessons with a description of what you're about to do. Relevant lessons may prevent mistakes or suggest better approaches.",
        rationale="Knowledge exists to be used. Querying before acting surfaces lessons that can save time, prevent bugs, and improve solutions. Acting first and querying never wastes the knowledge graph.",
        parent_id="mgcp-usage",
        tags=["mgcp", "query", "proactive", "critical"],
        examples=[
            Example(
                label="bad",
                code="# User: 'Fix the authentication bug'\n# Immediately start debugging without querying\nread_file('auth.py')",
                explanation="Missed opportunity to surface lessons about auth bugs, debugging strategies",
            ),
            Example(
                label="good",
                code="# User: 'Fix the authentication bug'\nquery_lessons('debugging authentication issues')\n# Now debug with relevant lessons in mind",
                explanation="Query first surfaces relevant debugging lessons and project-specific auth notes",
            ),
        ],
    ),

    # =========================================================================
    # MGCP KNOWLEDGE MAINTENANCE - Keep the knowledge graph healthy
    # =========================================================================
    Lesson(
        id="mgcp-check-before-adding",
        trigger="add lesson, add to catalogue, store, save, record, remember",
        action="BEFORE adding new knowledge, search for existing similar content: (1) query_lessons to check for similar lessons, (2) search_catalogue to check for similar catalogue items. If similar exists, use refine_lesson or update the existing item instead of creating duplicates.",
        rationale="Duplicate knowledge fragments the graph. One refined lesson is better than three similar ones. Checking first prevents pollution and keeps knowledge consolidated.",
        parent_id="mgcp-usage",
        tags=["mgcp", "maintenance", "duplicates", "quality"],
        examples=[
            Example(
                label="bad",
                code="# Learning about API validation\nadd_lesson(id='validate-api-input', ...)\n# Later, add another similar one\nadd_lesson(id='check-api-responses', ...)\n# Now have two overlapping lessons",
                explanation="Created duplicates instead of checking and refining",
            ),
            Example(
                label="good",
                code="# Learning about API validation\nquery_lessons('API validation')\n# Found 'verify-api-response' exists\nrefine_lesson(lesson_id='verify-api-response', refinement='Also validate request bodies, not just responses')",
                explanation="Searched first, refined existing lesson instead of duplicating",
            ),
        ],
    ),
    Lesson(
        id="mgcp-refine-not-duplicate",
        trigger="refine, improve, update lesson, enhance, add to existing, already exists",
        action="When a lesson exists but needs improvement, use refine_lesson to add new insight. Pass the lesson_id and a refinement string explaining the new knowledge. Optionally update the action text with new_action if the core instruction should change.",
        rationale="Refinement preserves lesson history (versions) and consolidates knowledge. Creating a new lesson fragments knowledge and loses the connection to prior learning.",
        parent_id="mgcp-usage",
        tags=["mgcp", "refinement", "maintenance"],
    ),
    Lesson(
        id="mgcp-link-related-lessons",
        trigger="related, connected, depends on, prerequisite, alternative, complements, see also",
        action="When lessons are related, call link_lessons to connect them. Choose relationship_type: 'prerequisite' (A before B), 'complements' (A+B together), 'alternative' (A or B), 'related' (similar topic), 'specializes' (A is specific case of B). This enables spider_lessons traversal.",
        rationale="Isolated lessons are less valuable than connected ones. Links enable graph traversal - when one lesson is found, related lessons surface automatically via spider_lessons.",
        parent_id="mgcp-usage",
        tags=["mgcp", "graph", "relationships", "linking"],
        examples=[
            Example(
                label="good",
                code="# Just added 'validate-jwt-tokens' lesson\n# It relates to existing 'verify-api-response' lesson\nlink_lessons(\n    lesson_id_a='validate-jwt-tokens',\n    lesson_id_b='verify-api-response',\n    relationship_type='complements',\n    context='authentication'\n)",
                explanation="New lesson linked to existing, enabling graph traversal",
            ),
        ],
    ),
    Lesson(
        id="mgcp-spider-for-context",
        trigger="related lessons, more context, what else, connected, explore, dig deeper",
        action="When you find a relevant lesson, call spider_lessons with its ID to discover connected knowledge. Set depth=2 for moderate exploration or depth=3+ for thorough research. This traverses the knowledge graph to surface related lessons.",
        rationale="One lesson often leads to others. Spider traversal surfaces the cluster of related knowledge, giving richer context than a single lesson query.",
        parent_id="mgcp-usage",
        tags=["mgcp", "graph", "traversal", "exploration"],
    ),
    Lesson(
        id="mgcp-verify-storage",
        trigger="did it save, was it stored, confirm, verify, check it worked",
        action="After adding or refining knowledge, verify it was stored correctly: (1) For lessons: query_lessons with terms that should match, (2) For catalogue items: search_catalogue or get_catalogue_item to confirm. This closes the feedback loop.",
        rationale="Storage can fail silently or store differently than expected. Verification confirms the knowledge will surface when needed and catches issues immediately.",
        parent_id="mgcp-usage",
        tags=["mgcp", "verification", "feedback", "quality"],
    ),

    # =========================================================================
    # MGCP CATALOGUE ITEM TYPES - Guidance for each catalogue type
    # =========================================================================
    Lesson(
        id="mgcp-record-security-notes",
        trigger="security, vulnerability, CVE, exploit, risk, sensitive, injection, XSS, auth bypass",
        action="When discovering a security concern, call add_catalogue_security_note with: title, description, severity (info/low/medium/high/critical), status (open/mitigated/accepted/resolved), and mitigation if known. Security knowledge must be project-scoped.",
        rationale="Security issues are critical project-specific knowledge. Recording them ensures they're tracked, not forgotten, and communicated to future sessions working on the same codebase.",
        parent_id="mgcp-usage",
        tags=["mgcp", "catalogue", "security"],
    ),
    Lesson(
        id="mgcp-record-conventions",
        trigger="convention, naming, style, pattern, always do, never do, our way, standard, rule",
        action="When establishing or discovering a coding convention, call add_catalogue_convention with: title, rule (the actual convention), category (naming/style/structure/testing/git), and examples. Conventions are project-specific standards.",
        rationale="Conventions ensure consistency across a codebase. Recording them prevents style drift and helps new contributors (including future LLM sessions) follow established patterns.",
        parent_id="mgcp-usage",
        tags=["mgcp", "catalogue", "conventions", "style"],
    ),
    Lesson(
        id="mgcp-record-error-patterns",
        trigger="error, exception, stack trace, fix for, solution, when you see, how to fix",
        action="When solving an error that may recur, call add_catalogue_error_pattern with: error_signature (what the error looks like), cause (root cause), solution (how to fix), and related_files. This creates a project-specific troubleshooting guide.",
        rationale="Errors recur. Recording the signature→cause→solution mapping saves future debugging time. The next session hitting the same error can find the solution instantly.",
        parent_id="mgcp-usage",
        tags=["mgcp", "catalogue", "errors", "debugging"],
        examples=[
            Example(
                label="good",
                code="add_catalogue_error_pattern(\n    project_path='/path/to/project',\n    error_signature='ConnectionRefusedError: [Errno 111] Connection refused',\n    cause='Redis server not running',\n    solution='Start Redis: docker-compose up -d redis',\n    related_files='src/cache.py, docker-compose.yml'\n)",
                explanation="Future sessions seeing this error can find the solution immediately",
            ),
        ],
    ),
    Lesson(
        id="mgcp-record-dependencies",
        trigger="library, framework, package, dependency, using, installed, requires, import",
        action="When adding or noting a significant dependency, call add_catalogue_dependency with: name, purpose (why it's used in this project), dep_type (framework/library/tool), version, docs_url, and notes about project-specific usage patterns.",
        rationale="Dependencies are project-specific context. Recording why a library was chosen and how it's used helps future sessions understand the codebase and make informed decisions about updates.",
        parent_id="mgcp-usage",
        tags=["mgcp", "catalogue", "dependencies"],
    ),

    # =========================================================================
    # GIT WORKFLOW LESSONS
    # =========================================================================
    Lesson(
        id="git-practices",
        trigger="git, commit, push, pull, branch, merge, version control",
        action="Follow consistent git practices: atomic commits, descriptive messages, verify changes before committing",
        rationale="Good git hygiene makes collaboration easier and debugging simpler. Bad commits pollute history and make bisecting difficult.",
        tags=["meta", "git", "workflow"],
    ),
    Lesson(
        id="query-before-git-operations",
        trigger="commit, push, git commit, git push, let's commit, ready to commit, PR, pull request",
        action="BEFORE performing any git operation (commit, push, PR), call query_lessons with 'git commit' or 'git workflow' to surface project-specific preferences like attribution rules, commit message formats, or branch conventions.",
        rationale="Different projects have different git conventions. Querying first surfaces any project-specific rules that override defaults.",
        parent_id="git-practices",
        tags=["git", "workflow", "proactive"],
    ),
    Lesson(
        id="lint-before-commit",
        trigger="commit, write code, implement, edit files, fix bugs, code changes",
        action="Run linting (ruff check, eslint, etc.) BEFORE committing code changes, not after CI fails. Catch issues while the code is fresh in context rather than debugging lint failures later.",
        rationale="Lint errors discovered in CI require context switching back to code you've already mentally moved on from. Running linter locally catches issues immediately when they're trivial to fix.",
        parent_id="git-practices",
        tags=["git", "linting", "code-quality", "ci"],
        examples=[
            Example(
                label="bad",
                code="# Write code, commit, push\n# CI fails with lint errors\n# Now have to context-switch back",
                explanation="Delayed feedback loop wastes time and mental energy",
            ),
            Example(
                label="good",
                code="# Write code\n# Run: ruff check src/\n# Fix any issues immediately\n# Commit clean code",
                explanation="Immediate feedback, issues fixed while context is fresh",
            ),
        ],
    ),
    Lesson(
        id="verify-before-push",
        trigger="push, git push, about to push, ready to push",
        action="Before pushing: (1) run tests locally, (2) run linter, (3) check git diff for debug code or secrets, (4) verify you're pushing to the correct branch. Pushing broken code blocks others.",
        rationale="Pushing untested code to shared branches breaks CI and blocks teammates. A few minutes of local verification saves hours of team disruption.",
        parent_id="git-practices",
        tags=["git", "verification", "ci"],
    ),

    # =========================================================================
    # WORKFLOW MANAGEMENT LESSONS
    # =========================================================================
    Lesson(
        id="mgcp-workflow-management",
        trigger="workflow, create workflow, new workflow, custom workflow, process, checklist",
        action="Use MGCP workflows to encode repeatable processes. Workflows surface the right lessons at the right time by linking lessons to specific steps.",
        rationale="Workflows turn scattered lessons into structured guidance. Instead of hoping the right lesson is queried, workflows guarantee it's surfaced at the right step.",
        parent_id="mgcp-usage",
        tags=["mgcp", "workflows", "process"],
    ),
    Lesson(
        id="mgcp-query-workflows-first",
        trigger="implement, fix, add feature, debug, refactor, work on, build, create",
        action="At the START of any coding task, call query_workflows with a description of the task. If a workflow matches (>50% relevance), activate it by calling get_workflow and following each step. If no match, proceed without a workflow.",
        rationale="Workflows encode hard-won knowledge about what goes wrong. Following a workflow prevents common mistakes. Not all tasks need workflows - simple changes can proceed directly.",
        parent_id="mgcp-workflow-management",
        tags=["mgcp", "workflows", "proactive"],
        examples=[
            Example(
                label="good",
                code="# User: 'Add user authentication'\nquery_workflows('implementing authentication')\n# Returns feature-development at 65% relevance\nget_workflow('feature-development')\n# Now follow Research -> Plan -> Document -> Execute -> Test -> Review",
                explanation="Workflow guides you through proven steps with relevant lessons at each",
            ),
        ],
    ),
    Lesson(
        id="mgcp-create-custom-workflows",
        trigger="new workflow, create workflow, repetitive task, same steps, process template, checklist",
        action="When you find yourself repeating the same process across tasks, create a custom workflow: (1) create_workflow with id, name, description, trigger keywords, (2) add_workflow_step for each step with checklist items, (3) link_lesson_to_workflow_step to attach relevant lessons. This codifies your process for reuse.",
        rationale="Workflows capture process knowledge that's otherwise tribal. A workflow for 'database migrations' or 'API endpoint additions' ensures consistency and surfaces relevant lessons automatically.",
        parent_id="mgcp-workflow-management",
        tags=["mgcp", "workflows", "process", "customization"],
    ),
    Lesson(
        id="mgcp-update-workflow-triggers",
        trigger="workflow didn't match, wrong workflow, should have matched, update trigger, refine workflow",
        action="When a task description SHOULD have matched a workflow but didn't (or matched the wrong one), use update_workflow to refine the trigger keywords. Add the words that should have matched. This is iterative learning - workflows improve over time.",
        rationale="Semantic matching isn't perfect. When it fails, updating triggers teaches the system your vocabulary. Over time, workflows match more reliably.",
        parent_id="mgcp-workflow-management",
        tags=["mgcp", "workflows", "refinement", "learning"],
        examples=[
            Example(
                label="good",
                code="# Task: 'modernize the button styles'\n# query_workflows returns no match\n# But this IS feature development!\nupdate_workflow(\n    workflow_id='feature-development',\n    trigger='new feature, implement, add, build, create, modernize, improve, style, UI'\n)\n# Now 'modernize' and 'style' will match",
                explanation="Updating triggers teaches the system your vocabulary",
            ),
        ],
    ),

    # =========================================================================
    # CLARIFICATION AND QUALITY LESSONS
    # =========================================================================
    Lesson(
        id="mgcp-clarify-before-storing",
        trigger="unclear, ambiguous, not sure, might be, could be, depends on, maybe, what kind of",
        action="Before storing knowledge (lessons, catalogue items, workflows), clarify ambiguities. Ask questions to understand: (1) Is this universal or project-specific? (2) What exactly triggers this? (3) What's the precise action? Vague knowledge pollutes the graph.",
        rationale="Ambiguous lessons surface at wrong times and give unclear guidance. Spending a moment to clarify before storing saves future confusion and keeps the knowledge graph clean.",
        parent_id="mgcp-usage",
        tags=["mgcp", "quality", "clarification"],
        examples=[
            Example(
                label="bad",
                code="add_lesson(id='handle-errors', trigger='errors', action='Handle errors properly')",
                explanation="Too vague - when does it apply? What's 'properly'?",
            ),
            Example(
                label="good",
                code="# First clarify: What kind of errors? What's the context?\n# Then store specific, actionable knowledge\nadd_lesson(id='handle-api-errors', trigger='API, request, response, error', action='Catch specific HTTP error codes (4xx client errors, 5xx server errors) and provide actionable error messages to users')",
                explanation="Specific trigger, specific action, clear guidance",
            ),
        ],
    ),
    Lesson(
        id="mgcp-actionable-triggers",
        trigger="trigger, when to apply, activate, surface lesson, keyword",
        action="Write triggers as comma-separated keywords that would appear when the lesson is relevant. Include synonyms and related terms. Test by asking: 'If someone searches these words, should this lesson surface?'",
        rationale="Triggers determine when lessons are found. Too narrow misses relevant queries. Too broad pollutes results. Good triggers balance precision and recall.",
        parent_id="mgcp-usage",
        tags=["mgcp", "lessons", "quality", "triggers"],
        examples=[
            Example(
                label="bad",
                code="trigger='authentication'  # Too narrow",
                explanation="Misses 'login', 'auth', 'sign in', 'credentials'",
            ),
            Example(
                label="good",
                code="trigger='authentication, login, auth, sign in, credentials, session, JWT, OAuth'",
                explanation="Includes synonyms and related concepts",
            ),
        ],
    ),
    Lesson(
        id="mgcp-imperative-actions",
        trigger="action, what to do, instruction, lesson action, guidance",
        action="Write lesson actions as imperative commands starting with a verb: 'Validate...', 'Check...', 'Use...', 'Avoid...'. NOT observations like 'X is important' or 'Consider X'. Actions should be directly executable.",
        rationale="Lessons are instructions, not observations. 'Validate input before processing' is actionable. 'Input validation is important' is not. Imperative actions tell you exactly what to do.",
        parent_id="mgcp-usage",
        tags=["mgcp", "lessons", "quality", "actions"],
        examples=[
            Example(
                label="bad",
                code="action='Error handling is important for good user experience'",
                explanation="Observation, not instruction - doesn't tell you what to DO",
            ),
            Example(
                label="good",
                code="action='Catch specific exceptions and return user-friendly error messages with actionable next steps'",
                explanation="Imperative - tells you exactly what to do",
            ),
        ],
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

    # MGCP knowledge storage type relationships
    ("mgcp-usage", "mgcp-knowledge-storage-types", "prerequisite", "Understand MGCP usage before storage type distinctions"),
    ("mgcp-knowledge-storage-types", "lessons-are-generic-knowledge", "prerequisite", "Understand storage types before lesson guidelines"),
    ("mgcp-knowledge-storage-types", "catalogue-for-project-specific", "prerequisite", "Understand storage types before catalogue guidelines"),
    ("mgcp-knowledge-storage-types", "workflow-links-for-process-guidance", "prerequisite", "Understand storage types before workflow guidelines"),
    ("lessons-are-generic-knowledge", "catalogue-for-project-specific", "complements", "Lessons and catalogue work together - one for generic, one for specific"),
    ("lessons-are-generic-knowledge", "mgcp-add-reusable-lessons", "related", "Both concern when and how to add lessons"),
    ("catalogue-for-project-specific", "mgcp-record-decisions", "related", "Both concern project-specific knowledge storage"),
    ("catalogue-for-project-specific", "mgcp-record-gotchas", "related", "Both concern project-specific knowledge storage"),
    ("catalogue-for-project-specific", "mgcp-record-couplings", "related", "Both concern project-specific knowledge storage"),

    # MGCP session lifecycle relationships
    ("mgcp-usage", "mgcp-session-start", "prerequisite", "Understand MGCP before session start procedures"),
    ("mgcp-usage", "mgcp-query-before-action", "prerequisite", "Understand MGCP before query patterns"),
    ("mgcp-session-start", "mgcp-query-before-action", "sequence_next", "After session start, query before each action"),
    ("mgcp-session-start", "mgcp-save-on-shutdown", "complements", "Session start and shutdown are bookends"),
    ("mgcp-query-before-action", "mgcp-check-before-adding", "complements", "Query before acting, check before storing"),

    # MGCP maintenance relationships
    ("mgcp-usage", "mgcp-check-before-adding", "prerequisite", "Understand MGCP before maintenance practices"),
    ("mgcp-usage", "mgcp-refine-not-duplicate", "prerequisite", "Understand MGCP before refinement"),
    ("mgcp-usage", "mgcp-link-related-lessons", "prerequisite", "Understand MGCP before linking"),
    ("mgcp-usage", "mgcp-spider-for-context", "prerequisite", "Understand MGCP before graph traversal"),
    ("mgcp-usage", "mgcp-verify-storage", "prerequisite", "Understand MGCP before verification"),
    ("mgcp-check-before-adding", "mgcp-refine-not-duplicate", "sequence_next", "Check for duplicates, then refine if found"),
    ("mgcp-refine-not-duplicate", "mgcp-link-related-lessons", "sequence_next", "After refining, link to related lessons"),
    ("mgcp-link-related-lessons", "mgcp-spider-for-context", "complements", "Linking enables spider traversal"),
    ("mgcp-add-reusable-lessons", "mgcp-verify-storage", "sequence_next", "After adding, verify it was stored"),
    ("lessons-are-generic-knowledge", "mgcp-check-before-adding", "complements", "Check existing before adding new"),

    # MGCP catalogue item relationships
    ("mgcp-usage", "mgcp-record-security-notes", "prerequisite", "Understand MGCP before security notes"),
    ("mgcp-usage", "mgcp-record-conventions", "prerequisite", "Understand MGCP before conventions"),
    ("mgcp-usage", "mgcp-record-error-patterns", "prerequisite", "Understand MGCP before error patterns"),
    ("mgcp-usage", "mgcp-record-dependencies", "prerequisite", "Understand MGCP before dependencies"),
    ("catalogue-for-project-specific", "mgcp-record-security-notes", "related", "Security notes are project-specific"),
    ("catalogue-for-project-specific", "mgcp-record-conventions", "related", "Conventions are project-specific"),
    ("catalogue-for-project-specific", "mgcp-record-error-patterns", "related", "Error patterns are project-specific"),
    ("catalogue-for-project-specific", "mgcp-record-dependencies", "related", "Dependencies are project-specific"),
    ("mgcp-record-security-notes", "mgcp-record-gotchas", "related", "Both document important project knowledge"),
    ("mgcp-record-conventions", "mgcp-record-gotchas", "related", "Conventions and gotchas both guide behavior"),
    ("mgcp-record-error-patterns", "mgcp-record-gotchas", "related", "Error patterns often capture gotchas"),

    # Git workflow relationships
    ("git-practices", "query-before-git-operations", "prerequisite", "Understand git practices before specific git triggers"),
    ("git-practices", "lint-before-commit", "prerequisite", "Understand git practices before linting workflow"),
    ("git-practices", "verify-before-push", "prerequisite", "Understand git practices before push verification"),
    ("query-before-git-operations", "mgcp-save-before-commit", "complements", "Query lessons and save context both happen before commit"),
    ("lint-before-commit", "verify-before-push", "sequence_next", "Lint before commit, verify before push"),
    ("lint-before-commit", "testing", "related", "Both concern code quality verification"),
    ("verify-before-push", "testing", "related", "Both concern verifying code before sharing"),

    # Workflow management relationships
    ("mgcp-usage", "mgcp-workflow-management", "prerequisite", "Understand MGCP before workflow management"),
    ("mgcp-workflow-management", "mgcp-query-workflows-first", "prerequisite", "Understand workflows before querying them"),
    ("mgcp-workflow-management", "mgcp-create-custom-workflows", "prerequisite", "Understand workflows before creating them"),
    ("mgcp-workflow-management", "mgcp-update-workflow-triggers", "prerequisite", "Understand workflows before updating triggers"),
    ("mgcp-query-workflows-first", "mgcp-query-before-action", "complements", "Query workflows and query lessons both happen before action"),
    ("mgcp-create-custom-workflows", "mgcp-update-workflow-triggers", "sequence_next", "Create workflow, then refine triggers over time"),
    ("mgcp-update-workflow-triggers", "mgcp-refine-not-duplicate", "related", "Both concern iterative improvement of existing knowledge"),
    ("workflow-links-for-process-guidance", "mgcp-create-custom-workflows", "related", "Both concern workflow structure and guidance"),

    # Clarification and quality relationships
    ("mgcp-usage", "mgcp-clarify-before-storing", "prerequisite", "Understand MGCP before quality guidelines"),
    ("mgcp-usage", "mgcp-actionable-triggers", "prerequisite", "Understand MGCP before trigger quality"),
    ("mgcp-usage", "mgcp-imperative-actions", "prerequisite", "Understand MGCP before action quality"),
    ("mgcp-clarify-before-storing", "lessons-are-generic-knowledge", "complements", "Clarify before storing, then choose correct storage"),
    ("mgcp-clarify-before-storing", "mgcp-check-before-adding", "sequence_next", "Clarify first, then check for duplicates"),
    ("mgcp-actionable-triggers", "mgcp-imperative-actions", "complements", "Good triggers and good actions make good lessons"),
    ("mgcp-actionable-triggers", "mgcp-add-reusable-lessons", "related", "Trigger quality is key to lesson utility"),
    ("mgcp-imperative-actions", "mgcp-add-reusable-lessons", "related", "Action quality is key to lesson utility"),
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
