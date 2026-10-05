"""The shipped bootstrap data must be internally consistent.

`mgcp-bootstrap` seeds a new install from these YAML files. They are the only
knowledge that travels with the repository: a lesson written during a session
lives in that machine's `~/.mgcp/lessons.db` and reaches nobody else.

That split is easy to forget, and forgetting it produces a specific bug. A
workflow step here referenced `audit-the-install-not-your-venv`, a lesson that
existed only in the author's local store. On a fresh install the step pointed at
nothing, and `get_workflow_step(expand_lessons=True)` would have returned a step
whose knowledge was missing. Nothing checked, so nothing said so.

These tests check what must hold for a fresh seed to make sense.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

BOOTSTRAP_DIR = Path(__file__).parent.parent / "src" / "mgcp" / "bootstrap_data"


def _load_all() -> list[tuple[Path, dict]]:
    docs = []
    for path in sorted(BOOTSTRAP_DIR.rglob("*.yaml")):
        loaded = yaml.safe_load(path.read_text())
        docs.append((path, loaded or {}))
    return docs


@pytest.fixture(scope="module")
def docs() -> list[tuple[Path, dict]]:
    found = _load_all()
    assert found, f"no bootstrap YAML found under {BOOTSTRAP_DIR}"
    return found


@pytest.fixture(scope="module")
def defined_lessons(docs) -> dict:
    """Every lesson id the bootstrap data defines, mapped to its file."""
    defined = {}
    for path, doc in docs:
        for lesson in doc.get("lessons") or []:
            if lesson.get("id"):
                defined[lesson["id"]] = path
    return defined


@pytest.fixture(scope="module")
def workflow_steps(docs) -> list[tuple[Path, str, str, list]]:
    """(file, workflow id, step id, lesson references) for every step."""
    steps = []
    for path, doc in docs:
        for workflow in doc.get("workflows") or []:
            for step in workflow.get("steps") or []:
                steps.append((path, workflow.get("id"), step.get("id"),
                              step.get("lessons") or []))
    return steps


def test_every_file_parses(docs):
    for path, doc in docs:
        assert isinstance(doc, dict), f"{path.name} is not a mapping"


def test_lesson_ids_are_unique(docs):
    """A duplicate id means one definition silently wins on seed."""
    seen: dict[str, Path] = {}
    clashes = []
    for path, doc in docs:
        for lesson in doc.get("lessons") or []:
            lid = lesson.get("id")
            if not lid:
                continue
            if lid in seen:
                clashes.append(f"{lid} in {seen[lid].name} and {path.name}")
            seen[lid] = path
    assert not clashes, f"duplicate lesson ids: {clashes}"


def test_no_workflow_step_references_a_missing_lesson(workflow_steps, defined_lessons):
    """The check this file exists for.

    A step that names a lesson the bootstrap data does not define is a step whose
    knowledge is absent on every fresh install, while looking complete here.
    """
    dangling = []
    for path, wf_id, step_id, lessons in workflow_steps:
        for ref in lessons:
            lid = ref.get("lesson_id")
            if lid and lid not in defined_lessons:
                dangling.append(f"{path.name}:{wf_id}/{step_id} -> {lid}")
    assert not dangling, (
        "workflow steps reference lessons that no bootstrap file defines, so a "
        f"fresh install gets a step with no knowledge behind it: {dangling}"
    )


def test_every_parent_id_exists(defined_lessons, docs):
    """A parent that does not exist breaks the category hierarchy on seed."""
    broken = []
    for path, doc in docs:
        for lesson in doc.get("lessons") or []:
            parent = lesson.get("parent_id")
            if parent and parent not in defined_lessons:
                broken.append(f"{path.name}:{lesson.get('id')} -> {parent}")
    assert not broken, f"lessons whose parent_id is undefined: {broken}"


def test_relationship_endpoints_exist(docs, defined_lessons):
    """A link to a lesson that does not exist cannot be created on seed."""
    broken = []
    for path, doc in docs:
        for rel in doc.get("relationships") or []:
            for key in ("from", "to", "from_id", "to_id",
                        "lesson_id_a", "lesson_id_b"):
                target = rel.get(key)
                if target and target not in defined_lessons:
                    broken.append(f"{path.name}: {key}={target}")
    assert not broken, f"relationships pointing at undefined lessons: {broken}"


def test_every_lesson_has_a_trigger_and_an_action(docs):
    """A lesson with no trigger cannot be retrieved, which makes it dead weight."""
    weak = []
    for path, doc in docs:
        for lesson in doc.get("lessons") or []:
            if not (lesson.get("trigger") or "").strip():
                weak.append(f"{path.name}:{lesson.get('id')} has no trigger")
            if not (lesson.get("action") or "").strip():
                weak.append(f"{path.name}:{lesson.get('id')} has no action")
    assert not weak, weak


def test_workflow_step_orders_are_unique_and_contiguous(docs):
    """A repeated order makes step sequence depend on file order."""
    problems = []
    for path, doc in docs:
        for workflow in doc.get("workflows") or []:
            orders = [s.get("order") for s in workflow.get("steps") or []]
            if not orders:
                continue
            if len(set(orders)) != len(orders):
                problems.append(f"{path.name}:{workflow.get('id')} repeats an order: {orders}")
            elif sorted(orders) != list(range(1, len(orders) + 1)):
                problems.append(
                    f"{path.name}:{workflow.get('id')} orders are not 1..n: {sorted(orders)}")
    assert not problems, problems
