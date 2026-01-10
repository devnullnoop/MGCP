"""Development bootstrap - software development lessons, practices, and workflows.

This module contains domain-specific knowledge about:
- Security best practices (OWASP)
- Verification and testing patterns
- Error handling
- Git workflows
- Development workflows (feature-development, bug-fix, secure-code-review)

These lessons apply to software development tasks specifically.
"""

from .models import Example, Lesson, Workflow, WorkflowStep, WorkflowStepLesson

# =============================================================================
# DEVELOPMENT LESSONS
# Software development best practices and patterns
# =============================================================================

DEV_LESSONS = [
    # =========================================================================
    # ROOT CATEGORIES
    # =========================================================================
    Lesson(
        id="security",
        trigger="security, secure, vulnerability, attack, exploit, injection, xss, csrf",
        action="Validate all input. Escape all output. Use parameterized queries. Encrypt secrets at rest. Trust nothing from outside your system boundary.",
        rationale="Security flaws compound. A single SQL injection exposes your entire database. A single XSS enables session hijacking. Cost to fix post-breach: reputation, legal liability, user trust—all unrecoverable.",
        tags=["meta", "security", "quality"],
    ),
    Lesson(
        id="verification",
        trigger="verify, check, validate, confirm, ensure, test",
        action="Log and inspect actual values before processing. Check types and nullability. Validate preconditions explicitly. Trust nothing implicitly.",
        rationale="Assumptions that fail silently produce corrupt data that propagates. By the time symptoms appear, the root cause is buried. Verify at the boundary, not after the damage.",
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
        action="Read official docs for the CURRENT version before writing code. Verify example code actually runs. Check changelogs for breaking changes in recent releases.",
        rationale="Stack Overflow answers rot. APIs break between versions. Copying outdated examples embeds vulnerabilities and deprecated patterns that break later.",
        tags=["meta", "research", "apis"],
    ),
    Lesson(
        id="testing",
        trigger="test, debug, verify, check, validate",
        action="Write tests with explicit expected outputs BEFORE integrating. Run tests after every change. Red test = stop and fix immediately.",
        rationale="Bugs discovered during integration require debugging across boundaries. Bugs discovered in isolation have one-line fixes. Test early or debug late—the choice costs 10x either way.",
        tags=["meta", "testing", "quality"],
    ),
    Lesson(
        id="error-handling",
        trigger="error, exception, fail, crash, bug, handle",
        action="Catch specific exceptions by type. Include context in messages: what failed, which value, where to look. Log stack traces internally. Return generic messages externally.",
        rationale="'Error occurred' is useless. 'User 4521 not found in auth_db during login' is actionable. The difference is hours of debugging vs. minutes.",
        tags=["meta", "errors", "quality"],
    ),

    # =========================================================================
    # VERIFICATION CHILDREN
    # =========================================================================
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

    # =========================================================================
    # API RESEARCH CHILDREN
    # =========================================================================
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

    # =========================================================================
    # TESTING CHILDREN
    # =========================================================================
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

    # =========================================================================
    # ERROR HANDLING CHILDREN
    # =========================================================================
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

    # =========================================================================
    # SECURITY CHILDREN
    # =========================================================================
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
    # OWASP SECURE CODING PRACTICES
    # Based on OWASP Secure Coding Practices Quick Reference Guide
    # =========================================================================

    # --- INPUT VALIDATION ---
    Lesson(
        id="owasp-input-validation",
        trigger="input, validate, sanitize, user input, form data, request, parameter, OWASP",
        action="Validate ALL input server-side using allowlists, not blocklists. Reject invalid input completely - don't attempt to sanitize and use it.",
        rationale="Client-side validation is bypassable. Blocklists miss edge cases. Attempting to sanitize malicious input often fails. Rejection is safer than transformation.",
        parent_id="security",
        tags=["security", "owasp", "input-validation"],
        examples=[
            Example(
                label="bad",
                code="# Blocklist approach - will miss edge cases\nif '<script>' not in user_input:\n    process(user_input)",
                explanation="Blocklist misses <SCRIPT>, <scr<script>ipt>, and countless other bypasses",
            ),
            Example(
                label="good",
                code="# Allowlist approach - only accept known-good patterns\nimport re\nif re.match(r'^[a-zA-Z0-9_-]+$', username):\n    process(username)\nelse:\n    raise ValueError('Invalid username format')",
                explanation="Allowlist defines exactly what's acceptable, rejects everything else",
            ),
        ],
    ),
    Lesson(
        id="owasp-centralized-validation",
        trigger="validation, input handling, form processing, request handling, middleware",
        action="Create centralized input validation routines shared across the application. Don't duplicate validation logic in multiple places.",
        rationale="Duplicated validation leads to inconsistencies where one path validates and another doesn't. Centralized validation ensures consistent security controls.",
        parent_id="owasp-input-validation",
        tags=["security", "owasp", "input-validation", "architecture"],
    ),
    Lesson(
        id="owasp-canonicalize-before-validate",
        trigger="encoding, unicode, UTF-8, character set, canonicalization, normalize",
        action="Canonicalize input to a common character set (UTF-8) BEFORE validation. Decode all encoded input before checking.",
        rationale="Attackers use encoding tricks (%2e%2e/, Unicode normalization) to bypass validation. Canonicalize first, then validate the normalized form.",
        parent_id="owasp-input-validation",
        tags=["security", "owasp", "input-validation", "encoding"],
        examples=[
            Example(
                label="bad",
                code="# Validates before decoding - bypassable\nif '../' not in path:\n    read_file(urllib.parse.unquote(path))  # %2e%2e%2f bypasses check",
                explanation="Validation happens on encoded form, but decoded form is used",
            ),
            Example(
                label="good",
                code="# Decode first, then validate\ndecoded_path = urllib.parse.unquote(path)\nif '../' in decoded_path or not decoded_path.startswith('/safe/'):\n    raise ValueError('Invalid path')\nread_file(decoded_path)",
                explanation="Decode first, then validate the actual value that will be used",
            ),
        ],
    ),

    # --- OUTPUT ENCODING ---
    Lesson(
        id="owasp-contextual-output-encoding",
        trigger="output, encode, escape, render, template, HTML, JavaScript, URL, CSS, XSS",
        action="Encode output based on the CONTEXT where it appears: HTML body, HTML attribute, JavaScript, URL, CSS. Each context requires different encoding.",
        rationale="HTML encoding doesn't protect JavaScript contexts. URL encoding doesn't protect HTML. Wrong encoding = XSS vulnerability.",
        parent_id="security",
        tags=["security", "owasp", "output-encoding", "xss"],
        examples=[
            Example(
                label="bad",
                code="# HTML encoding doesn't protect JS context\nreturn f'<script>var name = \"{html.escape(user_name)}\";</script>'",
                explanation="HTML escaping doesn't prevent JS injection via quotes or backslashes",
            ),
            Example(
                label="good",
                code="# Use JSON encoding for JS context\nimport json\nreturn f'<script>var name = {json.dumps(user_name)};</script>'",
                explanation="JSON encoding properly escapes for JavaScript string context",
            ),
        ],
    ),
    Lesson(
        id="owasp-encode-all-untrusted",
        trigger="untrusted data, user data, external data, third-party, API response",
        action="Treat ALL data from outside your trust boundary as untrusted and encode it before output. This includes database data, API responses, and file contents - not just direct user input.",
        rationale="Stored XSS occurs when previously-stored malicious data is rendered without encoding. Data from databases and APIs can contain injection payloads.",
        parent_id="owasp-contextual-output-encoding",
        tags=["security", "owasp", "output-encoding", "xss"],
    ),

    # --- AUTHENTICATION ---
    Lesson(
        id="owasp-authentication-fundamentals",
        trigger="authentication, login, password, credential, sign in, auth, OWASP",
        action="Use standard, tested authentication libraries. Never implement your own password hashing or session management from scratch.",
        rationale="Authentication is security-critical and easy to get wrong. Battle-tested libraries handle edge cases like timing attacks, secure comparisons, and proper hashing.",
        parent_id="security",
        tags=["security", "owasp", "authentication"],
        examples=[
            Example(
                label="bad",
                code="# Rolling your own password check\nif hashlib.md5(password).hexdigest() == stored_hash:\n    login(user)",
                explanation="MD5 is broken, no salt, vulnerable to timing attacks",
            ),
            Example(
                label="good",
                code="# Use a proper library\nfrom passlib.hash import argon2\nif argon2.verify(password, stored_hash):\n    login(user)",
                explanation="Argon2 is current best practice, library handles timing-safe comparison",
            ),
        ],
    ),
    Lesson(
        id="owasp-password-storage",
        trigger="password, hash, store password, bcrypt, argon2, scrypt, pbkdf2",
        action="Store passwords using Argon2id, bcrypt, scrypt, or PBKDF2 with high work factors. Never store plaintext, MD5, SHA1, or unsalted hashes.",
        rationale="Modern password hashing algorithms are intentionally slow and use salts to prevent rainbow table attacks. MD5/SHA1 are too fast and enable bulk cracking.",
        parent_id="owasp-authentication-fundamentals",
        tags=["security", "owasp", "authentication", "passwords"],
        examples=[
            Example(
                label="good",
                code="# Argon2id with appropriate parameters\nfrom argon2 import PasswordHasher\nph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)\nhash = ph.hash(password)",
                explanation="Argon2id with memory-hard parameters resists GPU/ASIC attacks",
            ),
        ],
    ),
    Lesson(
        id="owasp-auth-fail-securely",
        trigger="login failed, authentication error, invalid credentials, wrong password",
        action="Authentication failures must not reveal which part failed. Use generic messages like 'Invalid credentials' - never 'User not found' or 'Wrong password'.",
        rationale="Specific error messages enable username enumeration. Attackers can harvest valid usernames, then focus password attacks on confirmed accounts.",
        parent_id="owasp-authentication-fundamentals",
        tags=["security", "owasp", "authentication", "enumeration"],
        examples=[
            Example(
                label="bad",
                code="if not user_exists(username):\n    return 'User not found'\nif not check_password(password):\n    return 'Incorrect password'",
                explanation="Reveals whether username exists - enables enumeration",
            ),
            Example(
                label="good",
                code="if not authenticate(username, password):\n    return 'Invalid credentials'\n# Same message regardless of failure reason",
                explanation="Generic message prevents username enumeration",
            ),
        ],
    ),
    Lesson(
        id="owasp-account-lockout",
        trigger="brute force, rate limit, lockout, login attempts, failed attempts",
        action="Implement account lockout or progressive delays after failed login attempts. Log all authentication failures for monitoring.",
        rationale="Without lockout, attackers can brute-force passwords indefinitely. Progressive delays or lockouts make brute-force impractical.",
        parent_id="owasp-authentication-fundamentals",
        tags=["security", "owasp", "authentication", "brute-force"],
    ),
    Lesson(
        id="owasp-mfa",
        trigger="multi-factor, MFA, 2FA, two-factor, TOTP, authenticator, sensitive account",
        action="Implement multi-factor authentication for sensitive accounts and operations. Require re-authentication for critical actions like password changes or financial transactions.",
        rationale="Passwords alone are insufficient. MFA ensures account compromise requires multiple attack vectors. Re-auth for sensitive ops prevents session hijacking abuse.",
        parent_id="owasp-authentication-fundamentals",
        tags=["security", "owasp", "authentication", "mfa"],
    ),

    # --- SESSION MANAGEMENT ---
    Lesson(
        id="owasp-session-management",
        trigger="session, cookie, session ID, token, JSESSIONID, session fixation, OWASP",
        action="Use framework-provided session management. Generate cryptographically random session IDs (>=128 bits entropy). Regenerate session ID after login.",
        rationale="Weak session IDs are guessable. Session fixation attacks exploit pre-login session IDs. Framework implementations handle these edge cases.",
        parent_id="security",
        tags=["security", "owasp", "session"],
        examples=[
            Example(
                label="bad",
                code="# Predictable session ID\nsession_id = f'user_{user_id}_{timestamp}'",
                explanation="Predictable pattern - attacker can guess other sessions",
            ),
            Example(
                label="good",
                code="import secrets\nsession_id = secrets.token_urlsafe(32)  # 256 bits\n# Regenerate after login to prevent fixation\nrequest.session.regenerate()",
                explanation="Cryptographically random, regenerated after auth state change",
            ),
        ],
    ),
    Lesson(
        id="owasp-cookie-security",
        trigger="cookie, Set-Cookie, HttpOnly, Secure, SameSite, cookie attributes",
        action="Set ALL security attributes on session cookies: Secure (HTTPS only), HttpOnly (no JS access), SameSite=Strict or Lax (CSRF protection), and appropriate Domain/Path restrictions.",
        rationale="Missing Secure flag allows cookie theft via MITM. Missing HttpOnly enables XSS cookie theft. Missing SameSite enables CSRF attacks.",
        parent_id="owasp-session-management",
        tags=["security", "owasp", "session", "cookies"],
        examples=[
            Example(
                label="bad",
                code="response.set_cookie('session', token)",
                explanation="No security attributes - vulnerable to XSS, MITM, CSRF",
            ),
            Example(
                label="good",
                code="response.set_cookie(\n    'session', token,\n    secure=True,      # HTTPS only\n    httponly=True,    # No JS access\n    samesite='Lax',   # CSRF protection\n    max_age=3600      # 1 hour expiry\n)",
                explanation="All security attributes set",
            ),
        ],
    ),
    Lesson(
        id="owasp-session-timeout",
        trigger="session timeout, idle timeout, expiry, session expiration, logout",
        action="Implement both idle timeout (inactivity) and absolute timeout (max session lifetime). Terminate sessions completely on logout - invalidate server-side, not just client.",
        rationale="Long-lived sessions increase window for session hijacking. Client-only logout leaves session valid for stolen tokens.",
        parent_id="owasp-session-management",
        tags=["security", "owasp", "session", "timeout"],
    ),
    Lesson(
        id="owasp-session-id-exposure",
        trigger="session ID, URL, logs, error, exposure, leak",
        action="NEVER expose session IDs in URLs, error messages, or logs. Use POST for transmitting session data when cookies aren't possible.",
        rationale="Session IDs in URLs appear in browser history, referrer headers, and server logs - all accessible to attackers.",
        parent_id="owasp-session-management",
        tags=["security", "owasp", "session", "exposure"],
    ),

    # --- ACCESS CONTROL ---
    Lesson(
        id="owasp-access-control",
        trigger="authorization, access control, permission, role, privilege, RBAC, ABAC, OWASP",
        action="Enforce authorization on EVERY request server-side. Use a centralized access control component. Default to DENY - explicitly grant access, never implicitly allow.",
        rationale="Client-side authorization is bypassable. Scattered authorization logic leads to gaps. Implicit allow creates security holes when new resources are added.",
        parent_id="security",
        tags=["security", "owasp", "access-control", "authorization"],
        examples=[
            Example(
                label="bad",
                code="# Authorization only on some endpoints\n@app.route('/admin')\ndef admin():\n    if not is_admin(): abort(403)\n    ...\n\n@app.route('/admin/users')  # Forgot auth check!\ndef admin_users():\n    return get_all_users()",
                explanation="Scattered auth checks - easy to forget on new endpoints",
            ),
            Example(
                label="good",
                code="# Centralized middleware\n@app.before_request\ndef check_auth():\n    if request.path.startswith('/admin'):\n        if not is_admin():\n            abort(403)",
                explanation="Centralized check applies to all matching routes",
            ),
        ],
    ),
    Lesson(
        id="owasp-idor-prevention",
        trigger="IDOR, direct object reference, resource ID, user ID, document ID, authorization bypass",
        action="Always verify the requesting user is authorized to access the specific resource, not just the resource type. Check ownership or explicit permission grants.",
        rationale="IDOR vulnerabilities allow accessing other users' data by changing IDs in requests. Verifying 'user can access documents' isn't enough - verify 'user can access THIS document'.",
        parent_id="owasp-access-control",
        tags=["security", "owasp", "access-control", "idor"],
        examples=[
            Example(
                label="bad",
                code="@app.route('/documents/<doc_id>')\ndef get_document(doc_id):\n    return Document.query.get(doc_id)  # No ownership check!",
                explanation="Any authenticated user can access any document by ID",
            ),
            Example(
                label="good",
                code="@app.route('/documents/<doc_id>')\ndef get_document(doc_id):\n    doc = Document.query.get(doc_id)\n    if doc.owner_id != current_user.id:\n        abort(403)\n    return doc",
                explanation="Verify user owns or has explicit access to this specific document",
            ),
        ],
    ),
    Lesson(
        id="owasp-privilege-escalation",
        trigger="privilege escalation, admin, role, elevation, permission change",
        action="Never trust client-provided role or permission claims. Verify authorization server-side. Prevent users from modifying their own privilege level.",
        rationale="Attackers modify hidden form fields, cookies, or API parameters claiming admin roles. Server must be authoritative on permissions.",
        parent_id="owasp-access-control",
        tags=["security", "owasp", "access-control", "privilege"],
        examples=[
            Example(
                label="bad",
                code="# Trusting client-provided role\nuser_role = request.form.get('role', 'user')\nif user_role == 'admin':\n    grant_admin_access()",
                explanation="Attacker adds role=admin to form submission",
            ),
            Example(
                label="good",
                code="# Server-side role lookup\nuser_role = get_role_from_database(current_user.id)\nif user_role == 'admin':\n    grant_admin_access()",
                explanation="Role comes from trusted server-side source",
            ),
        ],
    ),

    # --- CRYPTOGRAPHY ---
    Lesson(
        id="owasp-cryptography",
        trigger="encryption, cryptography, crypto, AES, RSA, hash, OWASP, cipher",
        action="Use well-established cryptographic libraries and algorithms. Never implement your own cryptography. Use AES-256-GCM for symmetric, RSA-2048+/ECDSA P-256+ for asymmetric.",
        rationale="Cryptography is extremely easy to get wrong. Subtle implementation flaws (padding oracles, timing attacks) completely break security. Use vetted libraries.",
        parent_id="security",
        tags=["security", "owasp", "cryptography"],
        examples=[
            Example(
                label="bad",
                code="# Rolling your own XOR 'encryption'\ndef encrypt(data, key):\n    return bytes(a ^ b for a, b in zip(data, cycle(key)))",
                explanation="XOR cipher is trivially breakable, no authentication",
            ),
            Example(
                label="good",
                code="from cryptography.fernet import Fernet\nkey = Fernet.generate_key()\nf = Fernet(key)\nencrypted = f.encrypt(data)",
                explanation="Established library with authenticated encryption",
            ),
        ],
    ),
    Lesson(
        id="owasp-key-management",
        trigger="key management, encryption key, secret key, key rotation, key storage",
        action="Store cryptographic keys in secure key management systems (KMS, HSM, or secure vaults), not in code or config files. Implement key rotation procedures.",
        rationale="Keys in code are exposed in version control. Keys in config files are exposed in backups and logs. Proper key management limits blast radius of compromise.",
        parent_id="owasp-cryptography",
        tags=["security", "owasp", "cryptography", "key-management"],
    ),
    Lesson(
        id="owasp-random-generation",
        trigger="random, token, nonce, salt, UUID, session ID, CSPRNG",
        action="Use cryptographically secure random number generators (CSPRNG) for all security-sensitive values: tokens, session IDs, nonces, salts. Never use math.random() or similar.",
        rationale="Non-cryptographic RNGs are predictable. Attackers can predict future values or reconstruct internal state. Use secrets module (Python), crypto.randomBytes (Node), etc.",
        parent_id="owasp-cryptography",
        tags=["security", "owasp", "cryptography", "random"],
        examples=[
            Example(
                label="bad",
                code="import random\ntoken = ''.join(random.choices('abcdef0123456789', k=32))",
                explanation="random module is predictable - not suitable for security",
            ),
            Example(
                label="good",
                code="import secrets\ntoken = secrets.token_hex(32)  # 256 bits of entropy",
                explanation="secrets module uses OS CSPRNG",
            ),
        ],
    ),

    # --- DATA PROTECTION ---
    Lesson(
        id="owasp-data-protection",
        trigger="data protection, sensitive data, PII, personal data, encryption at rest, OWASP",
        action="Classify data by sensitivity. Encrypt sensitive data at rest and in transit. Implement data retention policies - delete when no longer needed.",
        rationale="Breaches happen. Encryption limits damage. Unnecessary data retention increases exposure. Classification enables appropriate controls.",
        parent_id="security",
        tags=["security", "owasp", "data-protection"],
    ),
    Lesson(
        id="owasp-sensitive-data-exposure",
        trigger="sensitive data, logs, cache, URL, GET parameter, browser history",
        action="Never put sensitive data in URLs (GET parameters), logs, error messages, or client-side caches. Use POST for sensitive submissions. Disable autocomplete on sensitive forms.",
        rationale="GET parameters appear in browser history, server logs, and referrer headers. Logs and caches are often less protected than primary data stores.",
        parent_id="owasp-data-protection",
        tags=["security", "owasp", "data-protection", "exposure"],
        examples=[
            Example(
                label="bad",
                code="# Password in URL - appears in logs, history\nredirect(f'/reset?token={token}&email={email}')",
                explanation="Sensitive data in URL is logged and cached everywhere",
            ),
            Example(
                label="good",
                code="# Use POST or store in session\nsession['reset_token'] = token\nredirect('/reset')",
                explanation="Sensitive data not exposed in URL",
            ),
        ],
    ),
    Lesson(
        id="owasp-https-everywhere",
        trigger="HTTPS, TLS, SSL, certificate, transport security, encryption in transit",
        action="Use HTTPS for ALL traffic, not just authentication. Validate TLS certificates properly. Use TLS 1.2+ with strong cipher suites. Implement HSTS.",
        rationale="HTTP traffic is readable by anyone on the network path. Partial HTTPS (login only) exposes session cookies on other pages. HSTS prevents downgrade attacks.",
        parent_id="owasp-data-protection",
        tags=["security", "owasp", "data-protection", "transport"],
    ),

    # --- DATABASE SECURITY ---
    Lesson(
        id="owasp-sql-injection",
        trigger="SQL, query, database, injection, SQLi, parameterized, prepared statement, OWASP",
        action="ALWAYS use parameterized queries or prepared statements. NEVER concatenate user input into SQL strings. Use ORM methods that parameterize automatically.",
        rationale="SQL injection is consistently in OWASP Top 10. String concatenation allows attackers to modify query logic, extract data, or destroy databases.",
        parent_id="security",
        tags=["security", "owasp", "database", "injection"],
        examples=[
            Example(
                label="bad",
                code="query = f\"SELECT * FROM users WHERE id = {user_id}\"\ncursor.execute(query)",
                explanation="String formatting - attacker can inject: 1 OR 1=1",
            ),
            Example(
                label="good",
                code="query = \"SELECT * FROM users WHERE id = %s\"\ncursor.execute(query, (user_id,))",
                explanation="Parameterized query - input is escaped automatically",
            ),
        ],
    ),
    Lesson(
        id="owasp-database-least-privilege",
        trigger="database connection, database user, database privileges, connection string",
        action="Use database accounts with minimum required privileges. Read-only operations should use read-only connections. Never use admin/root accounts for application access.",
        rationale="If SQL injection occurs, limited database privileges limit damage. A read-only connection can't DROP TABLES even if exploited.",
        parent_id="owasp-sql-injection",
        tags=["security", "owasp", "database", "least-privilege"],
    ),
    Lesson(
        id="owasp-stored-procedures",
        trigger="stored procedure, database abstraction, database API",
        action="Consider using stored procedures to abstract data access. This adds a layer between the application and base tables, limiting what queries can do.",
        rationale="Stored procedures can enforce business rules at the database level and limit the SQL that can be executed, reducing injection impact.",
        parent_id="owasp-sql-injection",
        tags=["security", "owasp", "database", "architecture"],
    ),

    # --- FILE MANAGEMENT ---
    Lesson(
        id="owasp-file-upload",
        trigger="file upload, upload, multipart, file type, MIME, extension, OWASP",
        action="Validate uploaded files by content (magic bytes), not just extension. Restrict allowed types to business-necessary formats. Store uploads outside web root. Rename files to prevent path traversal.",
        rationale="Extension checks are bypassable (.php.jpg). Uploads in web root can be executed. User-controlled filenames enable path traversal.",
        parent_id="security",
        tags=["security", "owasp", "files", "upload"],
        examples=[
            Example(
                label="bad",
                code="if filename.endswith('.jpg'):\n    path = f'/uploads/{filename}'  # Path traversal: ../../../etc/passwd.jpg\n    file.save(path)",
                explanation="Extension check only, user-controlled path",
            ),
            Example(
                label="good",
                code="import magic\nimport uuid\n\nmime = magic.from_buffer(file.read(1024), mime=True)\nif mime not in ['image/jpeg', 'image/png']:\n    abort(400)\nsafe_name = f'{uuid.uuid4()}.{mime.split(\"/\")[1]}'\npath = os.path.join(UPLOAD_DIR, safe_name)  # Outside web root\nfile.save(path)",
                explanation="Content validation, random filename, safe directory",
            ),
        ],
    ),
    Lesson(
        id="owasp-path-traversal",
        trigger="path traversal, directory traversal, LFI, local file inclusion, ../, dot dot slash",
        action="Never pass user input directly to file system operations. Use allowlists of permitted files, or map user input to indices. Validate paths are within expected directory after canonicalization.",
        rationale="Path traversal (../) allows reading arbitrary files including /etc/passwd, source code, and config files with secrets.",
        parent_id="security",
        tags=["security", "owasp", "files", "traversal"],
        examples=[
            Example(
                label="bad",
                code="template = request.args.get('template')\nreturn render_template(f'templates/{template}')\n# Attacker: ?template=../../../etc/passwd",
                explanation="User controls path - can escape intended directory",
            ),
            Example(
                label="good",
                code="ALLOWED_TEMPLATES = {'home': 'home.html', 'about': 'about.html'}\ntemplate_key = request.args.get('template')\nif template_key not in ALLOWED_TEMPLATES:\n    abort(404)\nreturn render_template(f'templates/{ALLOWED_TEMPLATES[template_key]}')",
                explanation="Allowlist maps user input to known-safe values",
            ),
        ],
    ),
    Lesson(
        id="owasp-file-execution",
        trigger="file execution, upload directory, executable, script execution",
        action="Disable script execution in upload directories. Configure web server to serve uploads as static files only. Never store uploads in code directories.",
        rationale="If attackers can upload and execute code (PHP, JSP, ASPX), they gain full server control. Treating uploads as static prevents execution.",
        parent_id="owasp-file-upload",
        tags=["security", "owasp", "files", "execution"],
    ),

    # --- ERROR HANDLING & LOGGING ---
    Lesson(
        id="owasp-error-handling",
        trigger="error handling, exception, stack trace, debug mode, verbose errors, OWASP",
        action="Display generic error messages to users. Log detailed errors server-side only. NEVER expose stack traces, SQL errors, or system paths in production.",
        rationale="Detailed errors reveal system internals - database structure, file paths, library versions - that help attackers plan exploits.",
        parent_id="security",
        tags=["security", "owasp", "errors"],
        examples=[
            Example(
                label="bad",
                code="try:\n    query_database()\nexcept Exception as e:\n    return f'Database error: {e}'  # Reveals DB details",
                explanation="Error details exposed to user",
            ),
            Example(
                label="good",
                code="try:\n    query_database()\nexcept Exception as e:\n    logger.exception('Database query failed')  # Full details in log\n    return 'An error occurred. Please try again.'  # Generic to user",
                explanation="Detailed logging, generic user message",
            ),
        ],
    ),
    Lesson(
        id="owasp-security-logging",
        trigger="security logging, audit log, authentication log, access log, security event",
        action="Log all security-relevant events: authentication attempts (success AND failure), access control failures, input validation failures, and administrative actions. Include timestamp, user, IP, and action.",
        rationale="Security logs enable incident detection and forensics. Without them, breaches go undetected and uninvestigated.",
        parent_id="security",
        tags=["security", "owasp", "logging", "audit"],
        examples=[
            Example(
                label="good",
                code="logger.info(\n    'auth_event',\n    extra={\n        'event_type': 'login_failed',\n        'username': username,\n        'ip': request.remote_addr,\n        'reason': 'invalid_password',\n        'timestamp': datetime.utcnow().isoformat()\n    }\n)",
                explanation="Structured security event with all relevant context",
            ),
        ],
    ),
    Lesson(
        id="owasp-log-injection",
        trigger="log injection, log forging, log tampering, CRLF injection",
        action="Sanitize user input before logging. Prevent newline injection that could forge log entries. Use structured logging formats (JSON) rather than string concatenation.",
        rationale="Attackers can inject fake log entries to hide their tracks or frame others. Newlines in usernames can create misleading log entries.",
        parent_id="owasp-security-logging",
        tags=["security", "owasp", "logging", "injection"],
    ),

    # --- CODE REVIEW CHECKLIST ITEMS ---
    Lesson(
        id="owasp-code-review-auth",
        trigger="code review, security review, review checklist, authentication review",
        action="During code review, verify: passwords use strong hashing (Argon2/bcrypt), session tokens have sufficient entropy (>=128 bits), re-auth required for sensitive operations, account lockout implemented.",
        rationale="Authentication flaws enable account takeover. Code review is the last line of defense before production.",
        parent_id="security",
        tags=["security", "owasp", "code-review", "authentication"],
    ),
    Lesson(
        id="owasp-code-review-authz",
        trigger="code review, authorization review, access control review",
        action="During code review, verify: every endpoint has authorization checks, authorization is enforced server-side, default is deny, no IDOR vulnerabilities (ownership verified for each resource).",
        rationale="Authorization bypass is a critical vulnerability class. Missing checks on even one endpoint can expose all data.",
        parent_id="security",
        tags=["security", "owasp", "code-review", "authorization"],
    ),
    Lesson(
        id="owasp-code-review-crypto",
        trigger="code review, cryptography review, encryption review",
        action="During code review, verify: modern algorithms (AES-256, RSA-2048+), proper key management, no hardcoded keys, CSPRNG for all random values, TLS configured correctly.",
        rationale="Cryptographic weaknesses may not cause immediate failures but completely compromise security. Review catches weak algorithms.",
        parent_id="security",
        tags=["security", "owasp", "code-review", "cryptography"],
    ),

    # =========================================================================
    # GIT WORKFLOW LESSONS
    # =========================================================================
    Lesson(
        id="git-practices",
        trigger="git, commit, push, pull, branch, merge, version control",
        action="One logical change per commit. Write messages that explain WHY, not WHAT. Run git diff before commit to catch debug code and secrets. Never force-push shared branches.",
        rationale="Atomic commits enable git bisect to pinpoint bugs. 'Fixed bug' is useless; 'Fix null check in auth middleware causing 500 on expired tokens' is searchable. Force-pushing shared branches rewrites history others depend on.",
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
    Lesson(
        id="pre-commit-documentation-review",
        trigger="commit, git commit, documentation, docs, readme, changelog",
        action="BEFORE committing changes, verify documentation is updated: (1) Check if code changes require doc updates, (2) Update CLAUDE.md if architecture or conventions changed, (3) Update README if user-facing behavior changed, (4) Add to CHANGELOG if user-visible. Never commit code changes that make docs stale.",
        rationale="Stale documentation is worse than no documentation - it actively misleads. Documentation debt compounds: the longer you wait, the harder it is to remember what changed and why. Commit docs with code, not 'later'.",
        parent_id="git-practices",
        tags=["git", "documentation", "workflow", "quality"],
    ),
]


# =============================================================================
# DEVELOPMENT WORKFLOWS
# =============================================================================

DEV_WORKFLOWS = [
    Workflow(
        id="feature-development",
        name="Feature Development",
        description="Use when implementing a new feature or significant change. Ensures research, planning, documentation, and testing.",
        trigger=(
            "implement, add, build, create, develop, make, write, set up, wire up, connect, "
            "new feature, add feature, add functionality, add capability, add support for, "
            "enhance, improve, optimize, upgrade, modernize, update, redesign, rework, "
            "refactor, restructure, reorganize, clean up, simplify, "
            "style, styling, UI, UX, interface, design, layout, component, view, screen, page, "
            "make it faster, speed up, performance, optimize queries, "
            "integrate, integration, hook up, plug in, "
            "build out, build the, create a, create new, develop the, work on, "
            "put together, get working, set this up"
        ),
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
                    WorkflowStepLesson(lesson_id="api-research", relevance="Must research APIs before using them", priority=1),
                    WorkflowStepLesson(lesson_id="check-api-versions", relevance="Version mismatches cause subtle bugs", priority=1),
                    WorkflowStepLesson(lesson_id="verify-api-response", relevance="Log actual responses during research", priority=2),
                    WorkflowStepLesson(lesson_id="dependency-security", relevance="Check dependencies for vulnerabilities", priority=2),
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
                    WorkflowStepLesson(lesson_id="verification", relevance="Plan what needs verification at each step", priority=2),
                    WorkflowStepLesson(lesson_id="error-handling", relevance="Design error handling into the plan", priority=2),
                ],
            ),
            WorkflowStep(
                id="document",
                name="Document Plan",
                description="Record the plan with sources. This prevents re-research and enables review.",
                order=3,
                guidance="Write down what you learned in research. Cite API docs and version numbers.",
                checklist=[
                    "Document approach in code comments or project docs",
                    "Record API versions and doc links",
                    "Note any assumptions being made",
                    "Record decisions and rationale",
                ],
                outputs=["documented plan", "cited sources", "recorded assumptions"],
                lessons=[
                    WorkflowStepLesson(lesson_id="error-context", relevance="Document context for future debugging", priority=3),
                ],
            ),
            WorkflowStep(
                id="execute",
                name="Execute",
                description="Implement step-by-step, testing as you go. Don't write everything then test at the end.",
                order=4,
                guidance="Implement one piece, test it, commit. Don't batch large changes.",
                checklist=[
                    "Implement incrementally",
                    "Test each piece before moving on",
                    "Commit working increments",
                    "Handle errors as they arise",
                    "Verify assumptions in code",
                ],
                outputs=["working implementation", "incremental commits"],
                lessons=[
                    WorkflowStepLesson(lesson_id="api-research", relevance="Re-verify API behavior during implementation", priority=1),
                    WorkflowStepLesson(lesson_id="verify-api-response", relevance="Log responses to debug issues", priority=1),
                    WorkflowStepLesson(lesson_id="testing", relevance="Test with known inputs as you implement", priority=1),
                    WorkflowStepLesson(lesson_id="verify-before-assert", relevance="Add verification checks in code", priority=2),
                    WorkflowStepLesson(lesson_id="specific-exceptions", relevance="Catch specific exceptions", priority=2),
                    WorkflowStepLesson(lesson_id="error-context", relevance="Include full context in error messages", priority=2),
                    WorkflowStepLesson(lesson_id="owasp-sql-injection", relevance="ALWAYS use parameterized queries", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-input-validation", relevance="Validate ALL input server-side", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-contextual-output-encoding", relevance="Encode output based on context", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-authentication-fundamentals", relevance="Use secure auth patterns", priority=2),
                    WorkflowStepLesson(lesson_id="owasp-access-control", relevance="Check authorization on EVERY endpoint", priority=2),
                ],
            ),
            WorkflowStep(
                id="test",
                name="Test",
                description="Comprehensive testing including edge cases. Verify the feature works as intended.",
                order=5,
                guidance="Test happy path, error cases, and edge cases. Verify error messages are helpful.",
                checklist=[
                    "Happy path works",
                    "Error cases handled gracefully",
                    "Edge cases covered (empty, null, boundary)",
                    "Error messages are informative",
                    "No regressions in existing functionality",
                ],
                outputs=["passing tests", "verified behavior"],
                lessons=[
                    WorkflowStepLesson(lesson_id="testing", relevance="Test with known inputs", priority=1),
                    WorkflowStepLesson(lesson_id="test-known-inputs", relevance="Known pairs make changes obvious", priority=1),
                    WorkflowStepLesson(lesson_id="test-edge-cases", relevance="Edge cases cause most bugs", priority=1),
                    WorkflowStepLesson(lesson_id="verification", relevance="Verify all assumptions", priority=2),
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
                    "Check for hardcoded secrets",
                    "Verify .env files are gitignored",
                ],
                outputs=["clean commit", "complete feature"],
                lessons=[
                    WorkflowStepLesson(lesson_id="verification", relevance="Final verification of assumptions", priority=2),
                    WorkflowStepLesson(lesson_id="error-handling", relevance="Verify error handling is complete", priority=2),
                    WorkflowStepLesson(lesson_id="no-hardcoded-secrets", relevance="Check for committed secrets", priority=1),
                    WorkflowStepLesson(lesson_id="pre-commit-documentation-review", relevance="Verify docs are updated", priority=1),
                ],
            ),
        ],
    ),
    Workflow(
        id="bug-fix",
        name="Bug Fix",
        description="Use when fixing a bug. Ensures understanding the root cause before applying a fix.",
        trigger=(
            "bug, fix, debug, issue, problem, defect, flaw, "
            "broken, not working, doesn't work, stopped working, fails, failing, failed, "
            "error, exception, crash, crashed, crashing, throws, throwing, "
            "investigate, troubleshoot, diagnose, figure out, track down, "
            "wrong, incorrect, unexpected, weird, strange, "
            "something's broken, it's broken, not right, acting up, "
            "what's wrong, having a problem, there's an issue, "
            "repair, resolve, address, correct, patch, "
            "regression, behavior changed, used to work"
        ),
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
                    WorkflowStepLesson(lesson_id="verification", relevance="Verify you can reproduce before fixing", priority=1),
                    WorkflowStepLesson(lesson_id="test-edge-cases", relevance="Check if bug occurs at boundaries", priority=2),
                ],
            ),
            WorkflowStep(
                id="investigate",
                name="Investigate",
                description="Find the root cause. Don't just fix the symptom.",
                order=2,
                guidance="Use logging or debugger to trace execution. Find where behavior diverges from expected.",
                checklist=[
                    "Identified root cause (not just symptom)",
                    "Understood why the bug occurs",
                    "Checked for similar issues elsewhere",
                ],
                outputs=["root cause identified", "understanding of failure mode"],
                lessons=[
                    WorkflowStepLesson(lesson_id="verify-api-response", relevance="Log actual values to see divergence", priority=1),
                    WorkflowStepLesson(lesson_id="error-context", relevance="Add context to understand the full picture", priority=2),
                ],
            ),
            WorkflowStep(
                id="fix",
                name="Fix",
                description="Apply the fix. Keep it minimal - don't refactor while fixing.",
                order=3,
                guidance="Fix only the bug. Don't add features or refactor. That's scope creep.",
                checklist=[
                    "Fix addresses root cause",
                    "Fix is minimal (no scope creep)",
                    "Added defensive checks if appropriate",
                ],
                outputs=["fix applied", "minimal diff"],
                lessons=[
                    WorkflowStepLesson(lesson_id="specific-exceptions", relevance="Add specific exception handling", priority=2),
                    WorkflowStepLesson(lesson_id="verify-before-assert", relevance="Add verification for the condition", priority=2),
                    WorkflowStepLesson(lesson_id="validate-input", relevance="Ensure validation is correct", priority=2),
                    WorkflowStepLesson(lesson_id="owasp-sql-injection", relevance="Use parameterized queries", priority=2),
                    WorkflowStepLesson(lesson_id="secure-error-messages", relevance="Don't expose sensitive details", priority=2),
                ],
            ),
            WorkflowStep(
                id="verify",
                name="Verify",
                description="Confirm the fix works and doesn't break anything else.",
                order=4,
                guidance="Test the fix with the original reproduction case. Run full test suite.",
                checklist=[
                    "Original bug no longer reproduces",
                    "Added test to prevent regression",
                    "Existing tests still pass",
                    "No new issues introduced",
                ],
                outputs=["verified fix", "regression test"],
                lessons=[
                    WorkflowStepLesson(lesson_id="testing", relevance="Test with reproduction case", priority=1),
                    WorkflowStepLesson(lesson_id="test-known-inputs", relevance="Add regression test", priority=1),
                ],
            ),
        ],
    ),
    Workflow(
        id="secure-code-review",
        name="Secure Code Review",
        description="Use when performing a security-focused code review. Based on OWASP Code Review Guide.",
        trigger="security review, code review, security audit, vulnerability check, pen test prep, OWASP review, secure code",
        tags=["security", "review", "owasp"],
        steps=[
            WorkflowStep(
                id="input-validation",
                name="Input Validation Review",
                description="Check all input handling for injection vulnerabilities.",
                order=1,
                guidance="Look for user input that flows into SQL, system commands, file paths, or HTML.",
                checklist=[
                    "All user inputs validated server-side",
                    "Using allowlists, not blocklists",
                    "Input canonicalized before validation",
                    "SQL uses parameterized queries",
                    "No command injection paths",
                    "File paths validated against traversal",
                ],
                outputs=["input validation findings", "injection vulnerability list"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-input-validation", relevance="Core input validation principles", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-sql-injection", relevance="Check all database queries", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-path-traversal", relevance="Check file operations", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-canonicalize-before-validate", relevance="Check encoding handling", priority=2),
                ],
            ),
            WorkflowStep(
                id="output-encoding",
                name="Output Encoding Review",
                description="Check all output contexts for proper encoding to prevent XSS.",
                order=2,
                guidance="Trace untrusted data to output points. Verify context-appropriate encoding.",
                checklist=[
                    "HTML output properly escaped",
                    "JavaScript contexts use JSON encoding",
                    "URL parameters properly encoded",
                    "Database data treated as untrusted",
                    "Template engine auto-escaping enabled",
                    "No raw HTML rendering of user data",
                ],
                outputs=["XSS vulnerability findings", "encoding gaps"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-contextual-output-encoding", relevance="Different contexts need different encoding", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-encode-all-untrusted", relevance="DB and API data is also untrusted", priority=1),
                    WorkflowStepLesson(lesson_id="escape-output", relevance="Basic output escaping", priority=2),
                ],
            ),
            WorkflowStep(
                id="authentication",
                name="Authentication Review",
                description="Review authentication mechanisms for weaknesses.",
                order=3,
                guidance="Check password storage, session ID generation, login failure messages, brute force protection.",
                checklist=[
                    "Passwords use Argon2/bcrypt/scrypt",
                    "Session IDs cryptographically random (>=128 bits)",
                    "Session regenerated after login",
                    "Generic error messages (no enumeration)",
                    "Account lockout or rate limiting",
                    "MFA available for sensitive accounts",
                    "Credentials transmitted over HTTPS only",
                ],
                outputs=["authentication weaknesses", "password storage findings"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-authentication-fundamentals", relevance="Use tested libraries", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-password-storage", relevance="Verify password hashing", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-auth-fail-securely", relevance="Check error messages", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-account-lockout", relevance="Verify brute force protection", priority=2),
                    WorkflowStepLesson(lesson_id="owasp-session-management", relevance="Check session ID generation", priority=1),
                ],
            ),
            WorkflowStep(
                id="authorization",
                name="Authorization Review",
                description="Review access control for bypasses and IDOR vulnerabilities.",
                order=4,
                guidance="Check every endpoint for authorization. Look for IDOR by tracing resource IDs.",
                checklist=[
                    "Every endpoint has authorization check",
                    "Authorization enforced server-side",
                    "Default is DENY, not ALLOW",
                    "Resource ownership verified (IDOR check)",
                    "No client-provided role/permission trust",
                    "Privilege changes require re-auth",
                    "Admin functions properly protected",
                ],
                outputs=["authorization bypasses", "IDOR vulnerabilities", "privilege escalation paths"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-access-control", relevance="Centralized, server-side, default deny", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-idor-prevention", relevance="Verify ownership for each resource", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-privilege-escalation", relevance="Don't trust client-provided roles", priority=1),
                    WorkflowStepLesson(lesson_id="least-privilege", relevance="Minimum permissions needed", priority=2),
                ],
            ),
            WorkflowStep(
                id="cryptography",
                name="Cryptography Review",
                description="Review cryptographic implementations for weak algorithms and key management.",
                order=5,
                guidance="Check for weak algorithms (MD5, SHA1, DES). Verify key storage. Check random generation.",
                checklist=[
                    "Modern algorithms (AES-256, RSA-2048+)",
                    "No MD5/SHA1 for security purposes",
                    "Keys not hardcoded in source",
                    "Keys stored in KMS/vault/env vars",
                    "CSPRNG for all security random values",
                    "TLS 1.2+ with strong ciphers",
                    "Proper key rotation procedures",
                ],
                outputs=["weak crypto findings", "key management issues"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-cryptography", relevance="Use established libraries", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-key-management", relevance="Keys in KMS/vault, not code", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-random-generation", relevance="CSPRNG for all security values", priority=1),
                    WorkflowStepLesson(lesson_id="no-hardcoded-secrets", relevance="No secrets in source code", priority=1),
                ],
            ),
            WorkflowStep(
                id="data-protection",
                name="Data Protection Review",
                description="Review handling of sensitive data including storage, transmission, and exposure.",
                order=6,
                guidance="Identify sensitive data flows. Check encryption at rest and in transit.",
                checklist=[
                    "Sensitive data classified and identified",
                    "Encrypted at rest",
                    "HTTPS for all transmission",
                    "Not in URLs or GET parameters",
                    "Not in logs or error messages",
                    "Proper data retention/deletion",
                    "HSTS header set",
                ],
                outputs=["data exposure findings", "encryption gaps"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-data-protection", relevance="Classify, encrypt, minimize retention", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-sensitive-data-exposure", relevance="No sensitive data in URLs, logs, errors", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-https-everywhere", relevance="HTTPS for all traffic", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-cookie-security", relevance="Secure, HttpOnly, SameSite on cookies", priority=2),
                ],
            ),
            WorkflowStep(
                id="error-logging",
                name="Error Handling & Logging Review",
                description="Review error handling for information disclosure and logging for security events.",
                order=7,
                guidance="Check error messages shown to users. Verify security events are logged.",
                checklist=[
                    "Generic errors to users",
                    "Detailed errors logged server-side",
                    "No stack traces in production",
                    "Auth failures logged with IP/user",
                    "Access control failures logged",
                    "Log injection prevented",
                    "Structured logging format",
                ],
                outputs=["error disclosure findings", "logging gaps"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-error-handling", relevance="Generic to users, detailed in logs", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-security-logging", relevance="Log all security-relevant events", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-log-injection", relevance="Sanitize before logging", priority=2),
                    WorkflowStepLesson(lesson_id="secure-error-messages", relevance="Don't expose internals", priority=2),
                ],
            ),
            WorkflowStep(
                id="file-handling",
                name="File Handling Review",
                description="Review file uploads, downloads, and filesystem access for vulnerabilities.",
                order=8,
                guidance="Check file upload validation. Verify upload storage is outside web root.",
                checklist=[
                    "Upload content validated (magic bytes)",
                    "Uploads stored outside web root",
                    "Files renamed (no user-controlled names)",
                    "No script execution in upload dirs",
                    "Path traversal prevented",
                    "File type restrictions enforced",
                    "Download authorization checked",
                ],
                outputs=["file handling vulnerabilities", "upload security findings"],
                lessons=[
                    WorkflowStepLesson(lesson_id="owasp-file-upload", relevance="Validate content, safe storage, rename files", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-path-traversal", relevance="No user input in file paths", priority=1),
                    WorkflowStepLesson(lesson_id="owasp-file-execution", relevance="No script execution in upload directories", priority=2),
                ],
            ),
        ],
    ),
]


# =============================================================================
# DEVELOPMENT RELATIONSHIPS
# Cross-links between development lessons
# =============================================================================

DEV_RELATIONSHIPS = [
    # Prerequisite relationships
    ("api-research", "check-api-versions", "prerequisite", "Must understand research process before checking versions"),
    ("api-research", "verify-api-response", "prerequisite", "Research APIs before logging responses"),
    ("testing", "test-known-inputs", "prerequisite", "Understand testing principles before specific patterns"),
    ("testing", "test-edge-cases", "prerequisite", "General testing before edge case specifics"),
    ("error-handling", "specific-exceptions", "prerequisite", "Understand error handling before exception specifics"),
    ("error-handling", "error-context", "prerequisite", "General handling before context patterns"),

    # Complements relationships
    ("check-api-versions", "verify-api-response", "complements", "Version checking and response logging work together"),
    ("test-known-inputs", "test-edge-cases", "complements", "Known inputs and edge cases are complementary tests"),
    ("specific-exceptions", "error-context", "complements", "Specific exceptions with contextual messages"),
    ("verify-before-assert", "error-context", "complements", "Verification with informative error messages"),

    # Related relationships
    ("verification", "testing", "related", "Both concern validating correctness"),
    ("verification", "api-research", "related", "Both concern verifying before acting"),
    ("testing", "error-handling", "related", "Testing often reveals error handling needs"),
    ("check-api-versions", "check-breaking-changes", "related", "Both concern dependency management"),
    ("verify-file-paths", "verify-calculations", "related", "Both are verification patterns"),

    # Sequence relationships
    ("api-research", "testing", "sequence_next", "After research, test your understanding"),
    ("test-known-inputs", "verify-before-assert", "sequence_next", "After testing, add runtime verification"),
    ("specific-exceptions", "test-edge-cases", "sequence_next", "After exception handling, test edge cases"),

    # Alternative relationships
    ("verify-before-assert", "verify-calculations", "alternative", "Different verification approaches"),

    # Security relationships
    ("security", "validate-input", "prerequisite", "Understand security principles before input validation"),
    ("security", "no-hardcoded-secrets", "prerequisite", "Understand security before secrets management"),
    ("security", "least-privilege", "prerequisite", "Understand security before privilege management"),
    ("security", "escape-output", "prerequisite", "Understand security before output handling"),
    ("validate-input", "escape-output", "complements", "Input validation and output escaping work together"),
    ("secure-error-messages", "error-context", "related", "Both deal with error information, different audiences"),
    ("dependency-security", "check-api-versions", "related", "Both concern dependency management and versions"),
    ("validate-input", "test-edge-cases", "complements", "Test edge cases to verify input validation"),
    ("no-hardcoded-secrets", "verify-file-paths", "related", "Both concern sensitive resource access"),

    # Git workflow relationships
    ("git-practices", "query-before-git-operations", "prerequisite", "Understand git practices before specific triggers"),
    ("git-practices", "lint-before-commit", "prerequisite", "Understand git practices before linting workflow"),
    ("git-practices", "verify-before-push", "prerequisite", "Understand git practices before push verification"),
    ("git-practices", "pre-commit-documentation-review", "prerequisite", "Understand git before doc review"),
    ("lint-before-commit", "verify-before-push", "sequence_next", "Lint before commit, verify before push"),
    ("lint-before-commit", "testing", "related", "Both concern code quality verification"),
    ("verify-before-push", "testing", "related", "Both concern verifying code before sharing"),
    ("pre-commit-documentation-review", "verify-before-push", "complements", "Both are pre-commit checks"),

    # OWASP Input Validation hierarchy
    ("security", "owasp-input-validation", "prerequisite", "Understand security before OWASP input validation"),
    ("owasp-input-validation", "owasp-centralized-validation", "prerequisite", "Understand input validation before centralization"),
    ("owasp-input-validation", "owasp-canonicalize-before-validate", "prerequisite", "Understand validation before canonicalization"),
    ("validate-input", "owasp-input-validation", "complements", "Both concern input validation from different angles"),
    ("owasp-input-validation", "owasp-sql-injection", "complements", "Input validation prevents SQL injection"),

    # OWASP Output Encoding hierarchy
    ("security", "owasp-contextual-output-encoding", "prerequisite", "Understand security before output encoding"),
    ("owasp-contextual-output-encoding", "owasp-encode-all-untrusted", "prerequisite", "Understand encoding before scope of untrusted data"),
    ("escape-output", "owasp-contextual-output-encoding", "complements", "Both concern output encoding"),
    ("owasp-input-validation", "owasp-contextual-output-encoding", "complements", "Input validation and output encoding work together"),

    # OWASP Authentication hierarchy
    ("security", "owasp-authentication-fundamentals", "prerequisite", "Understand security before authentication"),
    ("owasp-authentication-fundamentals", "owasp-password-storage", "prerequisite", "Understand auth before password storage"),
    ("owasp-authentication-fundamentals", "owasp-auth-fail-securely", "prerequisite", "Understand auth before failure handling"),
    ("owasp-authentication-fundamentals", "owasp-account-lockout", "prerequisite", "Understand auth before lockout"),
    ("owasp-authentication-fundamentals", "owasp-mfa", "prerequisite", "Understand auth before MFA"),
    ("owasp-password-storage", "owasp-auth-fail-securely", "complements", "Storage and failure handling work together"),
    ("owasp-account-lockout", "owasp-auth-fail-securely", "complements", "Lockout responds to auth failures"),

    # OWASP Session Management hierarchy
    ("security", "owasp-session-management", "prerequisite", "Understand security before session management"),
    ("owasp-session-management", "owasp-cookie-security", "prerequisite", "Understand sessions before cookie security"),
    ("owasp-session-management", "owasp-session-timeout", "prerequisite", "Understand sessions before timeouts"),
    ("owasp-session-management", "owasp-session-id-exposure", "prerequisite", "Understand sessions before exposure prevention"),
    ("owasp-cookie-security", "owasp-session-id-exposure", "complements", "Cookie security prevents session exposure"),
    ("owasp-authentication-fundamentals", "owasp-session-management", "sequence_next", "After authentication, manage sessions"),

    # OWASP Access Control hierarchy
    ("security", "owasp-access-control", "prerequisite", "Understand security before access control"),
    ("owasp-access-control", "owasp-idor-prevention", "prerequisite", "Understand access control before IDOR prevention"),
    ("owasp-access-control", "owasp-privilege-escalation", "prerequisite", "Understand access control before privilege escalation"),
    ("least-privilege", "owasp-access-control", "complements", "Both concern access control principles"),
    ("owasp-idor-prevention", "owasp-privilege-escalation", "complements", "Both are authorization bypass vulnerabilities"),
    ("owasp-authentication-fundamentals", "owasp-access-control", "sequence_next", "After authentication, check authorization"),

    # OWASP Cryptography hierarchy
    ("security", "owasp-cryptography", "prerequisite", "Understand security before cryptography"),
    ("owasp-cryptography", "owasp-key-management", "prerequisite", "Understand crypto before key management"),
    ("owasp-cryptography", "owasp-random-generation", "prerequisite", "Understand crypto before random generation"),
    ("no-hardcoded-secrets", "owasp-key-management", "complements", "Both concern secret/key management"),
    ("owasp-random-generation", "owasp-session-management", "complements", "Random generation needed for session IDs"),
    ("owasp-password-storage", "owasp-cryptography", "related", "Password hashing is a crypto operation"),

    # OWASP Data Protection hierarchy
    ("security", "owasp-data-protection", "prerequisite", "Understand security before data protection"),
    ("owasp-data-protection", "owasp-sensitive-data-exposure", "prerequisite", "Understand data protection before exposure prevention"),
    ("owasp-data-protection", "owasp-https-everywhere", "prerequisite", "Understand data protection before transport security"),
    ("owasp-session-id-exposure", "owasp-sensitive-data-exposure", "complements", "Session IDs are sensitive data"),
    ("owasp-https-everywhere", "owasp-cookie-security", "complements", "HTTPS needed for Secure cookie flag"),

    # OWASP Database Security hierarchy
    ("security", "owasp-sql-injection", "prerequisite", "Understand security before SQL injection prevention"),
    ("owasp-sql-injection", "owasp-database-least-privilege", "prerequisite", "Understand injection before DB privileges"),
    ("owasp-sql-injection", "owasp-stored-procedures", "prerequisite", "Understand injection before stored procedures"),
    ("validate-input", "owasp-sql-injection", "complements", "Input validation prevents SQL injection"),
    ("owasp-database-least-privilege", "least-privilege", "related", "Both concern least privilege principle"),

    # OWASP File Management hierarchy
    ("security", "owasp-file-upload", "prerequisite", "Understand security before file upload security"),
    ("security", "owasp-path-traversal", "prerequisite", "Understand security before path traversal prevention"),
    ("owasp-file-upload", "owasp-file-execution", "prerequisite", "Understand uploads before execution prevention"),
    ("owasp-path-traversal", "verify-file-paths", "complements", "Both concern file path security"),
    ("owasp-input-validation", "owasp-path-traversal", "complements", "Input validation prevents path traversal"),
    ("owasp-input-validation", "owasp-file-upload", "complements", "Input validation for file content"),

    # OWASP Error Handling hierarchy
    ("security", "owasp-error-handling", "prerequisite", "Understand security before error handling"),
    ("security", "owasp-security-logging", "prerequisite", "Understand security before security logging"),
    ("owasp-security-logging", "owasp-log-injection", "prerequisite", "Understand logging before log injection"),
    ("secure-error-messages", "owasp-error-handling", "complements", "Both concern secure error messages"),
    ("error-handling", "owasp-error-handling", "related", "Both concern error handling"),
    ("owasp-auth-fail-securely", "owasp-security-logging", "complements", "Log authentication failures"),
    ("owasp-input-validation", "owasp-log-injection", "related", "Input validation prevents log injection"),

    # OWASP Code Review lessons
    ("security", "owasp-code-review-auth", "prerequisite", "Understand security before auth code review"),
    ("security", "owasp-code-review-authz", "prerequisite", "Understand security before authz code review"),
    ("security", "owasp-code-review-crypto", "prerequisite", "Understand security before crypto code review"),
    ("owasp-authentication-fundamentals", "owasp-code-review-auth", "related", "Auth fundamentals inform auth review"),
    ("owasp-access-control", "owasp-code-review-authz", "related", "Access control informs authz review"),
    ("owasp-cryptography", "owasp-code-review-crypto", "related", "Crypto fundamentals inform crypto review"),
    ("owasp-code-review-auth", "owasp-code-review-authz", "sequence_next", "Review auth before authz"),
    ("owasp-code-review-authz", "owasp-code-review-crypto", "sequence_next", "Review authz before crypto"),
]
