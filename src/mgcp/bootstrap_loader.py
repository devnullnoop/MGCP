"""Load bootstrap lessons, workflows, and relationships from YAML files.

This module replaces the Python-based bootstrap_core.py and bootstrap_dev.py
with a YAML-based approach for better readability and maintainability.

Directory structure:
    bootstrap_data/
    ├── core/
    │   ├── lessons.yaml
    │   └── relationships.yaml
    └── dev/
        ├── security/
        │   ├── input-validation.yaml
        │   └── ...
        ├── architecture.yaml
        ├── testing.yaml
        ├── relationships.yaml
        └── workflows.yaml
"""

from pathlib import Path

import yaml

from .models import Example, Lesson, Workflow, WorkflowStep, WorkflowStepLesson

BOOTSTRAP_DIR = Path(__file__).parent / "bootstrap_data"


def _parse_lesson(data: dict) -> Lesson:
    """Parse a lesson dict from YAML into a Lesson model."""
    examples = []
    for ex in data.get("examples", []):
        examples.append(Example(
            label=ex["label"],
            code=ex["code"],
            explanation=ex.get("explanation"),
        ))

    return Lesson(
        id=data["id"],
        trigger=data["trigger"],
        action=data["action"],
        rationale=data.get("rationale"),
        tags=data.get("tags", []),
        parent_id=data.get("parent_id"),
        examples=examples,
    )


def _parse_workflow(data: dict) -> Workflow:
    """Parse a workflow dict from YAML into a Workflow model."""
    steps = []
    for step_data in data.get("steps", []):
        lessons = []
        for lesson_data in step_data.get("lessons", []):
            lessons.append(WorkflowStepLesson(
                lesson_id=lesson_data["lesson_id"],
                relevance=lesson_data["relevance"],
                priority=lesson_data.get("priority", 2),
            ))

        steps.append(WorkflowStep(
            id=step_data["id"],
            name=step_data["name"],
            description=step_data["description"],
            order=step_data["order"],
            guidance=step_data.get("guidance", ""),
            checklist=step_data.get("checklist", []),
            outputs=step_data.get("outputs", []),
            lessons=lessons,
        ))

    return Workflow(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        trigger=data["trigger"],
        steps=steps,
        tags=data.get("tags", []),
    )


def _load_yaml_file(path: Path) -> dict:
    """Load and parse a single YAML file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _collect_yaml_files(directory: Path, pattern: str = "*.yaml") -> list[Path]:
    """Recursively collect YAML files from a directory."""
    if not directory.exists():
        return []
    return sorted(directory.rglob(pattern))


def _is_relationship_file(path: Path) -> bool:
    """Check if a YAML file contains relationship data (not lessons)."""
    return "relationships" in path.stem


def _is_workflow_file(path: Path) -> bool:
    """Check if a YAML file contains workflow data (not lessons)."""
    return path.stem == "workflows"


def load_lessons(subdir: str | None = None) -> list[Lesson]:
    """Load lessons from YAML files in bootstrap_data/.

    Args:
        subdir: Optional subdirectory to load from (e.g., "core", "dev").
                If None, loads from all subdirectories.

    Returns:
        List of Lesson objects parsed from YAML files.
    """
    lessons = []
    base = BOOTSTRAP_DIR / subdir if subdir else BOOTSTRAP_DIR

    for yaml_path in _collect_yaml_files(base):
        if _is_relationship_file(yaml_path) or _is_workflow_file(yaml_path):
            continue

        data = _load_yaml_file(yaml_path)
        for lesson_data in data.get("lessons", []):
            lessons.append(_parse_lesson(lesson_data))

    return lessons


def load_relationships(subdir: str | None = None) -> list[tuple]:
    """Load relationship tuples from YAML files.

    Args:
        subdir: Optional subdirectory to load from (e.g., "core", "dev").
                If None, loads from all subdirectories.

    Returns:
        List of (source_id, target_id, rel_type, context) tuples.
    """
    relationships = []
    base = BOOTSTRAP_DIR / subdir if subdir else BOOTSTRAP_DIR

    for yaml_path in _collect_yaml_files(base):
        if not _is_relationship_file(yaml_path):
            continue

        data = _load_yaml_file(yaml_path)
        for rel in data.get("relationships", []):
            relationships.append((
                rel["source"],
                rel["target"],
                rel["type"],
                rel.get("context", ""),
            ))

    return relationships


def load_workflows(subdir: str | None = None) -> list[Workflow]:
    """Load workflows from YAML files.

    Args:
        subdir: Optional subdirectory to load from (e.g., "core", "dev").
                If None, loads from all subdirectories.

    Returns:
        List of Workflow objects parsed from YAML files.
    """
    workflows = []
    base = BOOTSTRAP_DIR / subdir if subdir else BOOTSTRAP_DIR

    for yaml_path in _collect_yaml_files(base):
        if yaml_path.name != "workflows.yaml":
            continue

        data = _load_yaml_file(yaml_path)
        for wf_data in data.get("workflows", []):
            workflows.append(_parse_workflow(wf_data))

    return workflows
