"""Data models for MGCP (Memory Graph Control Protocol)."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Example(BaseModel):
    """A good or bad example demonstrating a lesson."""

    label: Literal["good", "bad"]
    code: str
    explanation: str | None = None


# Relationship types for typed edges in the knowledge graph
RelationshipType = Literal[
    "related",        # General relationship (default, for backwards compatibility)
    "prerequisite",   # Must know/do this first
    "sequence_next",  # Do this after (workflow ordering)
    "alternative",    # Different approach to same problem
    "complements",    # Use together with
    "specializes",    # More specific version of
    "generalizes",    # More general version of
    "contradicts",    # Conflicting approaches
    "child_of",       # Hierarchical parent (can have multiple)
]


class Relationship(BaseModel):
    """A typed, weighted relationship to another lesson."""

    target: str = Field(..., description="ID of the related lesson")
    type: RelationshipType = Field(default="related", description="Type of relationship")
    weight: float = Field(default=0.5, ge=0.0, le=1.0, description="Strength of relationship 0-1")
    context: list[str] = Field(default_factory=list, description="Contexts where this relationship applies (e.g., ['ui', 'debugging'])")
    bidirectional: bool = Field(default=True, description="Whether this relationship goes both ways")


class Lesson(BaseModel):
    """A lesson learned from past LLM interactions."""

    id: str = Field(..., description="Unique identifier")
    trigger: str = Field(..., description="When this lesson applies (keywords/patterns)")
    action: str = Field(..., description="What to do (imperative)")
    rationale: str | None = Field(None, description="Why this matters")
    examples: list[Example] = Field(default_factory=list)
    version: int = Field(default=1, description="Refinement count")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_refined: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime | None = Field(None, description="Last retrieval time")
    usage_count: int = Field(default=0, description="Times retrieved")
    tags: list[str] = Field(default_factory=list)
    parent_id: str | None = Field(None, description="Primary parent lesson for hierarchy (deprecated, use relationships)")
    related_ids: list[str] = Field(default_factory=list, description="Cross-links (deprecated, use relationships)")
    relationships: list[Relationship] = Field(default_factory=list, description="Typed relationships to other lessons")

    def to_context(self) -> str:
        """Format lesson for inclusion in LLM context."""
        lines = [
            f"**{self.id}**: {self.action}",
        ]
        if self.rationale:
            lines.append(f"  Why: {self.rationale}")
        if self.examples:
            for ex in self.examples[:2]:  # Limit examples in context
                label = "✓" if ex.label == "good" else "✗"
                lines.append(f"  {label} {ex.code}")
        return "\n".join(lines)


class LessonSummary(BaseModel):
    """Lightweight lesson summary for listings."""

    id: str
    trigger: str
    action: str
    tags: list[str]
    usage_count: int


class QueryResult(BaseModel):
    """Result from a lesson query."""

    lesson: Lesson
    score: float = Field(..., description="Similarity score 0-1")
    source: Literal["semantic", "keyword", "graph"] = "semantic"


class ProjectTodo(BaseModel):
    """A todo item for a specific project."""

    content: str = Field(..., description="What needs to be done")
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    priority: int = Field(default=0, description="Priority 0-9, higher is more urgent")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notes: str | None = Field(None, description="Additional context or blockers")


class Dependency(BaseModel):
    """A library, framework, or tool used by the project."""

    name: str = Field(..., description="Package/library name")
    version: str | None = Field(None, description="Version constraint or exact version")
    purpose: str = Field(..., description="What it's used for in this project")
    docs_url: str | None = Field(None, description="Link to documentation")
    notes: str | None = Field(None, description="Project-specific usage notes")


class ArchitecturalNote(BaseModel):
    """A project-specific architectural decision or concept."""

    title: str = Field(..., description="Short title (e.g., 'MCP Server Restart Required')")
    description: str = Field(..., description="Full explanation")
    category: Literal["architecture", "convention", "gotcha", "security", "performance"] = "architecture"
    related_files: list[str] = Field(default_factory=list, description="Files this applies to")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SecurityNote(BaseModel):
    """A known security issue or consideration."""

    title: str = Field(..., description="Issue title")
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    description: str = Field(..., description="Details about the issue")
    status: Literal["open", "mitigated", "accepted", "resolved"] = "open"
    mitigation: str | None = Field(None, description="How it's being addressed")


class Convention(BaseModel):
    """A project coding convention or style rule."""

    title: str = Field(..., description="Short title (e.g., 'Snake case for functions')")
    rule: str = Field(..., description="The actual rule to follow")
    category: Literal["naming", "style", "structure", "testing", "git"] = "style"
    examples: list[str] = Field(default_factory=list, description="Quick examples")


class FileCoupling(BaseModel):
    """Files that must change together."""

    files: list[str] = Field(..., description="Files that are coupled (e.g., ['server.py', 'models.py'])")
    reason: str = Field(..., description="Why they're coupled")
    direction: Literal["bidirectional", "a_triggers_b"] = "bidirectional"


class Decision(BaseModel):
    """An architectural or design decision with rationale."""

    title: str = Field(..., description="Short title (e.g., 'Chose NetworkX over Neo4j')")
    decision: str = Field(..., description="What was decided")
    rationale: str = Field(..., description="Why this choice was made")
    alternatives: list[str] = Field(default_factory=list, description="What was considered")
    date: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ErrorPattern(BaseModel):
    """A common error and its solution."""

    error_signature: str = Field(..., description="What the error looks like (regex or text)")
    cause: str = Field(..., description="Root cause of the error")
    solution: str = Field(..., description="How to fix it")
    related_files: list[str] = Field(default_factory=list, description="Files where this error occurs")


class GenericCatalogueItem(BaseModel):
    """A flexible catalogue item for custom item types.

    Allows users to create new catalogue item types without modifying the schema.
    The item_type field defines the category (e.g., 'api_endpoint', 'env_var', 'migration').
    """

    item_type: str = Field(..., description="The type/category of this item (e.g., 'api_endpoint', 'env_var')")
    title: str = Field(..., description="Short title for the item")
    content: str = Field(..., description="Main content/description")
    metadata: dict[str, str] = Field(default_factory=dict, description="Additional key-value metadata")
    tags: list[str] = Field(default_factory=list, description="Tags for searchability")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectCatalogue(BaseModel):
    """Structured knowledge base for a project - the bootstrap guide."""

    # Technology stack
    languages: list[str] = Field(default_factory=list, description="Programming languages used")
    frameworks: list[Dependency] = Field(default_factory=list, description="Major frameworks")
    libraries: list[Dependency] = Field(default_factory=list, description="Key libraries")
    tools: list[Dependency] = Field(default_factory=list, description="Build tools, dev tools")

    # Architecture & patterns
    architecture_notes: list[ArchitecturalNote] = Field(default_factory=list, description="Key architectural decisions")
    patterns_used: list[str] = Field(default_factory=list, description="Design patterns (e.g., 'MVC', 'Repository')")

    # Development
    entry_points: dict[str, str] = Field(default_factory=dict, description="Key entry points (e.g., {'server': 'src/server.py'})")
    test_commands: list[str] = Field(default_factory=list, description="How to run tests")
    build_commands: list[str] = Field(default_factory=list, description="How to build/deploy")

    # Issues & security
    security_notes: list[SecurityNote] = Field(default_factory=list, description="Security considerations")
    known_issues: list[str] = Field(default_factory=list, description="Known bugs or limitations")

    # Quick reference
    key_concepts: dict[str, str] = Field(default_factory=dict, description="Domain concepts explained (e.g., {'Lesson': 'A reusable piece of knowledge...'})")

    # LLM-optimized knowledge (new)
    conventions: list[Convention] = Field(default_factory=list, description="Coding style rules and conventions")
    file_couplings: list[FileCoupling] = Field(default_factory=list, description="Files that must change together")
    decisions: list[Decision] = Field(default_factory=list, description="Architectural decisions with rationale")
    error_patterns: list[ErrorPattern] = Field(default_factory=list, description="Common errors and solutions")

    # Flexible custom items (extensible)
    custom_items: list["GenericCatalogueItem"] = Field(default_factory=list, description="Custom catalogue items of any type")


class ProjectContext(BaseModel):
    """Project-specific context for session continuity.

    Stores working state between sessions so Claude can resume work
    without re-scanning the entire project. Includes a catalogue of
    project-specific knowledge for quick bootstrapping.
    """

    project_id: str = Field(..., description="Unique project identifier (usually directory path hash)")
    project_name: str = Field(..., description="Human-readable project name")
    project_path: str = Field(..., description="Absolute path to project root")

    # Project catalogue - the bootstrap guide
    catalogue: ProjectCatalogue = Field(default_factory=ProjectCatalogue, description="Project knowledge base")

    # Active work state
    todos: list[ProjectTodo] = Field(default_factory=list, description="Active todo items")
    active_files: list[str] = Field(default_factory=list, description="Files currently being worked on")
    recent_decisions: list[str] = Field(default_factory=list, description="Recent architectural/design decisions")

    # Session tracking
    last_session_id: str | None = Field(None, description="Last session that worked on this project")
    last_accessed: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_count: int = Field(default=0, description="Number of sessions on this project")

    # Quick notes
    notes: str | None = Field(None, description="Freeform notes about current state")

    def to_context(self) -> str:
        """Format project context for LLM consumption."""
        lines = [
            f"## Project: {self.project_name}",
            f"Path: {self.project_path}",
            f"Sessions: {self.session_count} | Last: {self.last_accessed.strftime('%Y-%m-%d %H:%M')}",
        ]

        # Catalogue summary
        cat = self.catalogue
        if cat.languages:
            lines.append(f"\n**Stack**: {', '.join(cat.languages)}")
        if cat.frameworks:
            lines.append(f"**Frameworks**: {', '.join(f.name for f in cat.frameworks)}")
        if cat.patterns_used:
            lines.append(f"**Patterns**: {', '.join(cat.patterns_used)}")

        # Key architecture notes (gotchas)
        gotchas = [n for n in cat.architecture_notes if n.category == "gotcha"]
        if gotchas:
            lines.append("\n### ⚠️ Important Gotchas:")
            for note in gotchas[:3]:
                lines.append(f"  • **{note.title}**: {note.description[:100]}...")

        # Active security issues
        open_security = [s for s in cat.security_notes if s.status == "open"]
        if open_security:
            lines.append("\n### 🔒 Open Security Issues:")
            for sec in open_security[:3]:
                lines.append(f"  • [{sec.severity.upper()}] {sec.title}")

        # Conventions
        if cat.conventions:
            lines.append("\n### 📏 Conventions:")
            for conv in cat.conventions[:5]:
                lines.append(f"  • **{conv.title}**: {conv.rule}")

        # File couplings
        if cat.file_couplings:
            lines.append("\n### 🔗 File Couplings:")
            for coupling in cat.file_couplings[:3]:
                files_str = " ↔ ".join(coupling.files[:3])
                lines.append(f"  • {files_str}: {coupling.reason}")

        # Recent decisions
        if cat.decisions:
            lines.append("\n### 📋 Key Decisions:")
            for dec in cat.decisions[:3]:
                lines.append(f"  • **{dec.title}**: {dec.rationale[:80]}...")

        # Error patterns
        if cat.error_patterns:
            lines.append("\n### 🐛 Known Error Patterns:")
            for err in cat.error_patterns[:3]:
                lines.append(f"  • `{err.error_signature[:50]}` → {err.solution[:60]}...")

        # Entry points
        if cat.entry_points:
            lines.append("\n### Entry Points:")
            for name, path in list(cat.entry_points.items())[:5]:
                lines.append(f"  • {name}: `{path}`")

        # Active todos
        if self.todos:
            pending = [t for t in self.todos if t.status in ("pending", "in_progress")]
            if pending:
                lines.append("\n### Active Todos:")
                for todo in pending[:5]:
                    status_icon = "🔄" if todo.status == "in_progress" else "⏳"
                    lines.append(f"  {status_icon} {todo.content}")

        if self.active_files:
            lines.append(f"\n### Working on: {', '.join(self.active_files[:5])}")

        if self.recent_decisions:
            lines.append("\n### Recent Decisions:")
            for decision in self.recent_decisions[-3:]:
                lines.append(f"  • {decision}")

        if self.notes:
            lines.append(f"\n### Notes: {self.notes}")

        # Custom catalogue items (grouped by type)
        if cat.custom_items:
            grouped = {}
            for item in cat.custom_items:
                if item.item_type not in grouped:
                    grouped[item.item_type] = []
                grouped[item.item_type].append(item)

            for item_type, items in grouped.items():
                lines.append(f"\n### 📦 {item_type.replace('_', ' ').title()}:")
                for item in items[:5]:
                    lines.append(f"  • **{item.title}**: {item.content[:80]}...")

        return "\n".join(lines)