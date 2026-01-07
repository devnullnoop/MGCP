#!/usr/bin/env python3
"""Seed demo data for screenshots and demonstrations.

This creates realistic-looking sample data for the MGCP dashboard,
lessons page, and projects page to showcase the system's capabilities.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mgcp.models import (
    ArchitecturalNote,
    Convention,
    Decision,
    Dependency,
    ErrorPattern,
    FileCoupling,
    Lesson,
    ProjectContext,
    ProjectTodo,
    Relationship,
    SecurityNote,
)
from mgcp.persistence import LessonStore
from mgcp.vector_store import VectorStore
from mgcp.graph import LessonGraph


DEMO_LESSONS = [
    # Root categories
    Lesson(
        id="api-development",
        trigger="API, REST, endpoints, backend",
        action="Best practices for API development",
        rationale="APIs are the backbone of modern applications. Following consistent patterns improves maintainability.",
        tags=["api", "backend", "architecture"],
        usage_count=45,
    ),
    Lesson(
        id="testing-strategies",
        trigger="testing, tests, TDD, unit tests, integration",
        action="Comprehensive testing guidelines",
        rationale="Good test coverage prevents regressions and documents expected behavior.",
        tags=["testing", "quality"],
        usage_count=38,
    ),
    Lesson(
        id="security-practices",
        trigger="security, auth, authentication, authorization, vulnerabilities",
        action="Security-first development practices",
        rationale="Security issues are costly to fix after deployment. Build security in from the start.",
        tags=["security", "best-practices"],
        usage_count=52,
    ),
    Lesson(
        id="error-handling",
        trigger="errors, exceptions, error handling, try catch",
        action="Robust error handling patterns",
        rationale="Proper error handling improves debugging and user experience.",
        tags=["errors", "reliability"],
        usage_count=33,
    ),

    # API Development children
    Lesson(
        id="api-versioning",
        trigger="API versioning, breaking changes, backwards compatibility",
        action="Use URL path versioning (e.g., /v1/users) for public APIs. Include version in response headers.",
        rationale="Versioning allows evolving APIs without breaking existing clients.",
        tags=["api", "versioning"],
        parent_id="api-development",
        usage_count=28,
        relationships=[
            Relationship(target="api-documentation", type="complements", weight=0.8),
        ],
    ),
    Lesson(
        id="api-documentation",
        trigger="API docs, OpenAPI, Swagger, documentation",
        action="Generate OpenAPI specs from code. Keep docs in sync with implementation using automated tools.",
        rationale="Accurate documentation reduces integration friction and support burden.",
        tags=["api", "documentation"],
        parent_id="api-development",
        usage_count=22,
    ),
    Lesson(
        id="api-rate-limiting",
        trigger="rate limiting, throttling, API abuse",
        action="Implement rate limiting with sliding window algorithm. Return 429 with Retry-After header.",
        rationale="Protects against abuse and ensures fair resource allocation.",
        tags=["api", "security", "performance"],
        parent_id="api-development",
        usage_count=15,
        relationships=[
            Relationship(target="security-practices", type="specializes", weight=0.6),
        ],
    ),

    # Testing children
    Lesson(
        id="test-isolation",
        trigger="test isolation, flaky tests, test dependencies",
        action="Each test should be independent. Use fresh fixtures and clean up after tests.",
        rationale="Isolated tests are reliable and can run in parallel.",
        tags=["testing", "reliability"],
        parent_id="testing-strategies",
        usage_count=25,
    ),
    Lesson(
        id="mock-external-services",
        trigger="mocking, external APIs, test doubles",
        action="Mock external services at the HTTP boundary. Use recorded responses for deterministic tests.",
        rationale="External dependencies make tests slow and flaky.",
        tags=["testing", "mocking"],
        parent_id="testing-strategies",
        usage_count=19,
        relationships=[
            Relationship(target="test-isolation", type="complements", weight=0.7),
        ],
    ),

    # Security children
    Lesson(
        id="input-validation",
        trigger="input validation, sanitization, injection",
        action="Validate all inputs at system boundaries. Use allowlists over denylists. Never trust client data.",
        rationale="Input validation is the first line of defense against injection attacks.",
        tags=["security", "validation"],
        parent_id="security-practices",
        usage_count=41,
    ),
    Lesson(
        id="secrets-management",
        trigger="secrets, API keys, credentials, environment variables",
        action="Never commit secrets to git. Use environment variables or secret managers. Rotate credentials regularly.",
        rationale="Leaked secrets are a leading cause of breaches.",
        tags=["security", "secrets"],
        parent_id="security-practices",
        usage_count=35,
    ),
    Lesson(
        id="jwt-best-practices",
        trigger="JWT, tokens, authentication, session",
        action="Use short-lived access tokens with refresh tokens. Store in httpOnly cookies, not localStorage.",
        rationale="Proper JWT handling prevents token theft and session hijacking.",
        tags=["security", "authentication"],
        parent_id="security-practices",
        usage_count=29,
        relationships=[
            Relationship(target="input-validation", type="prerequisite", weight=0.5),
        ],
    ),

    # Error handling children
    Lesson(
        id="structured-logging",
        trigger="logging, structured logs, observability",
        action="Use structured JSON logging with consistent fields: timestamp, level, message, context, trace_id.",
        rationale="Structured logs enable efficient querying and alerting in production.",
        tags=["logging", "observability"],
        parent_id="error-handling",
        usage_count=31,
    ),
    Lesson(
        id="graceful-degradation",
        trigger="fallback, circuit breaker, resilience",
        action="Implement circuit breakers for external calls. Provide fallback responses when services are unavailable.",
        rationale="Graceful degradation maintains partial functionality during outages.",
        tags=["reliability", "resilience"],
        parent_id="error-handling",
        usage_count=17,
        relationships=[
            Relationship(target="mock-external-services", type="related", weight=0.4),
        ],
    ),
]


DEMO_PROJECT = ProjectContext(
    project_id="demo-project-001",
    project_name="E-Commerce Platform",
    project_path="/projects/ecommerce-platform",
    notes="Full-stack e-commerce application with React frontend and Python FastAPI backend. Currently refactoring payment integration.",
    active_files=[
        "src/api/payments.py",
        "src/services/stripe_client.py",
        "tests/test_payments.py",
    ],
    recent_decisions=[
        "Chose Stripe over PayPal for payment processing due to better API ergonomics",
        "Using Redis for session storage instead of database-backed sessions",
        "Adopted trunk-based development with feature flags",
    ],
    session_count=42,
    todos=[
        ProjectTodo(content="Implement webhook signature verification", priority=8, status="in_progress"),
        ProjectTodo(content="Add retry logic for failed payment captures", priority=6, status="pending"),
        ProjectTodo(content="Update API documentation for new endpoints", priority=4, status="pending"),
        ProjectTodo(content="Set up monitoring dashboards", priority=5, status="completed"),
    ],
)

# Add catalogue items
DEMO_PROJECT.catalogue.frameworks = [
    Dependency(name="FastAPI", purpose="REST API framework", version="0.104.0", docs_url="https://fastapi.tiangolo.com"),
    Dependency(name="React", purpose="Frontend UI library", version="18.2.0", docs_url="https://react.dev"),
    Dependency(name="SQLAlchemy", purpose="Database ORM", version="2.0.0"),
]

DEMO_PROJECT.catalogue.libraries = [
    Dependency(name="Pydantic", purpose="Data validation and settings", version="2.5.0"),
    Dependency(name="stripe", purpose="Payment processing SDK", version="7.0.0"),
    Dependency(name="redis", purpose="Caching and session storage", version="5.0.0"),
    Dependency(name="pytest", purpose="Testing framework", version="7.4.0"),
]

DEMO_PROJECT.catalogue.tools = [
    Dependency(name="Docker", purpose="Containerization", version="24.0"),
    Dependency(name="GitHub Actions", purpose="CI/CD pipeline"),
]

DEMO_PROJECT.catalogue.architecture_notes = [
    ArchitecturalNote(
        title="Payment Service Isolation",
        description="All payment logic is isolated in the payments module with its own error handling. External calls go through a dedicated client class.",
        category="architecture",
        related_files=["src/api/payments.py", "src/services/stripe_client.py"],
    ),
    ArchitecturalNote(
        title="Webhook Idempotency Required",
        description="Stripe webhooks may be delivered multiple times. Always check idempotency key before processing.",
        category="gotcha",
        related_files=["src/api/webhooks.py"],
    ),
    ArchitecturalNote(
        title="Database Connection Pooling",
        description="Using SQLAlchemy connection pool with max 20 connections. Increase for high-traffic periods.",
        category="performance",
        related_files=["src/db/connection.py"],
    ),
]

DEMO_PROJECT.catalogue.security_notes = [
    SecurityNote(
        title="PCI DSS Compliance",
        description="Never log or store raw card numbers. Use Stripe tokens exclusively.",
        severity="critical",
        status="mitigated",
        mitigation="All card handling delegated to Stripe.js and Payment Elements",
    ),
    SecurityNote(
        title="Rate Limiting on Auth Endpoints",
        description="Login and password reset endpoints need rate limiting to prevent brute force attacks.",
        severity="high",
        status="open",
    ),
    SecurityNote(
        title="CORS Configuration",
        description="CORS allows requests from localhost in development. Ensure production config is restrictive.",
        severity="medium",
        status="mitigated",
        mitigation="Environment-based CORS origins configuration",
    ),
]

DEMO_PROJECT.catalogue.conventions = [
    Convention(title="Snake Case Functions", rule="Use snake_case for all Python functions and variables", category="naming", examples=["get_user_by_id", "calculate_total_price"]),
    Convention(title="API Response Format", rule="All API responses use {data, error, meta} structure", category="style"),
    Convention(title="Test File Naming", rule="Test files mirror source structure with test_ prefix", category="testing", examples=["test_payments.py for payments.py"]),
    Convention(title="Commit Messages", rule="Use conventional commits: feat:, fix:, docs:, refactor:", category="git"),
]

DEMO_PROJECT.catalogue.decisions = [
    Decision(
        title="Chose Stripe over PayPal",
        decision="Use Stripe as primary payment processor",
        rationale="Better developer experience, cleaner API, superior documentation, and lower fees for our volume",
        alternatives=["PayPal", "Square", "Adyen"],
    ),
    Decision(
        title="Redis for Sessions",
        decision="Store sessions in Redis instead of PostgreSQL",
        rationale="Lower latency, automatic expiration, and reduces database load",
        alternatives=["PostgreSQL sessions", "JWT-only (stateless)"],
    ),
]

DEMO_PROJECT.catalogue.file_couplings = [
    FileCoupling(files=["src/models/order.py", "src/api/orders.py", "src/services/order_service.py"], reason="Order model changes require updates to API and service layer"),
    FileCoupling(files=["src/api/payments.py", "src/api/webhooks.py"], reason="Payment flow spans both endpoints"),
]

DEMO_PROJECT.catalogue.error_patterns = [
    ErrorPattern(
        error_signature="stripe.error.CardError",
        cause="Customer's card was declined",
        solution="Return user-friendly message. Do not expose raw Stripe error.",
        related_files=["src/services/stripe_client.py"],
    ),
    ErrorPattern(
        error_signature="sqlalchemy.exc.IntegrityError.*duplicate key",
        cause="Race condition causing duplicate insert",
        solution="Use SELECT FOR UPDATE or implement upsert logic",
        related_files=["src/db/"],
    ),
]


async def seed_demo_data():
    """Seed the database with demo data."""
    print("🌱 Seeding demo data for screenshots...")

    store = LessonStore()
    vector_store = VectorStore()
    graph = LessonGraph()

    # Check if demo data already exists
    existing = await store.get_lesson("api-development")
    if existing:
        print("⚠️  Demo data already exists. Skipping seed.")
        print("   To re-seed, delete existing demo lessons first.")
        return

    # Add lessons
    print(f"📚 Adding {len(DEMO_LESSONS)} demo lessons...")
    for lesson in DEMO_LESSONS:
        await store.add_lesson(lesson)
        vector_store.add_lesson(lesson)
        graph.add_lesson(lesson)
        print(f"   ✓ {lesson.id}")

    # Add project context
    print(f"📁 Adding demo project: {DEMO_PROJECT.project_name}")
    await store.save_project_context(DEMO_PROJECT)

    print("\n✅ Demo data seeded successfully!")
    print("\nTo capture screenshots:")
    print("  1. Start the web server: python -m mgcp.web_server")
    print("  2. Run: python scripts/capture_screenshots.py")
    print("  3. Screenshots will be saved to docs/screenshots/")


async def cleanup_demo_data():
    """Remove demo data."""
    print("🧹 Cleaning up demo data...")

    store = LessonStore()
    vector_store = VectorStore()

    for lesson in DEMO_LESSONS:
        deleted = await store.delete_lesson(lesson.id)
        if deleted:
            vector_store.remove_lesson(lesson.id)
            print(f"   ✓ Removed {lesson.id}")

    # Note: We don't delete the project context to preserve user's real data
    print("\n✅ Demo lessons removed.")
    print("   (Demo project context preserved)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed or cleanup demo data")
    parser.add_argument("--cleanup", action="store_true", help="Remove demo data instead of seeding")
    args = parser.parse_args()

    if args.cleanup:
        asyncio.run(cleanup_demo_data())
    else:
        asyncio.run(seed_demo_data())