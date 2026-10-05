"""The committed SBOM has to describe this project, or it is a false claim.

A software bill of materials (SBOM) lists every component shipped, so a reader
can check it against advisory feeds without installing anything. An SBOM that
drifts from the project is worse than none, because it answers that question
wrongly and nothing says so.

MGCP declares version ranges rather than pinned versions, so the exact resolved
set changes whenever an upstream project publishes. These tests therefore check
the things that must hold at all times, not byte equality with a fresh resolve:

1. It parses as CycloneDX and names this project.
2. Every direct dependency in pyproject.toml appears in it.
3. It is not older than the dependency list it claims to describe.

Point 3 is the one that catches real staleness, and it reads git history rather
than a date inside the file, because a date inside the file is one more thing to
forget to update.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SBOM_PATH = REPO_ROOT / "sbom.cdx.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def _declared_direct_dependencies() -> set[str]:
    """Dependency names from pyproject.toml, normalised.

    Parsed with a regular expression rather than a TOML library, because the
    comparison must work the same way in the hook, in CI and here, and the hook
    is stdlib-only on Python 3.11 where tomllib exists but the hook does not
    import project code.
    """
    text = PYPROJECT_PATH.read_text()
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text,
                      re.MULTILINE | re.DOTALL)
    assert block, "dependencies list not found in pyproject.toml"
    names = set()
    for raw in re.findall(r'"([^"]+)"', block.group(1)):
        name = re.split(r"[<>=!~\[;]", raw, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


@pytest.fixture(scope="module")
def sbom() -> dict:
    assert SBOM_PATH.exists(), (
        "sbom.cdx.json is missing. Regenerate it with the command in the "
        "README's Dependency security section."
    )
    return json.loads(SBOM_PATH.read_text())


def test_it_is_cyclonedx_and_names_this_project(sbom):
    assert sbom.get("bomFormat") == "CycloneDX"
    assert sbom.get("specVersion"), "no specVersion"
    root = (sbom.get("metadata") or {}).get("component") or {}
    assert root.get("name") == "mgcp", f"root component is {root.get('name')!r}"


def test_the_version_matches_the_package(sbom):
    """A bill of materials for a different version describes a different thing."""
    import mgcp

    root = (sbom.get("metadata") or {}).get("component") or {}
    assert root.get("version") == mgcp.__version__, (
        f"SBOM says {root.get('version')} and the package is {mgcp.__version__}. "
        "Regenerate the SBOM."
    )


def test_it_carries_no_volatile_fields(sbom):
    """Generated with --output-reproducible, so two runs are byte-identical.

    A serial number or timestamp that changes every run makes the file
    unverifiable: every regeneration looks like a change, so a real change
    cannot be told from noise.
    """
    assert "serialNumber" not in sbom
    assert "timestamp" not in (sbom.get("metadata") or {})


def test_every_direct_dependency_appears(sbom):
    """The check that catches adding a dependency and forgetting the SBOM."""
    listed = {
        (c.get("name") or "").lower().replace("_", "-")
        for c in sbom.get("components", [])
    }
    missing = _declared_direct_dependencies() - listed
    assert not missing, (
        f"declared in pyproject.toml but absent from the SBOM: {sorted(missing)}. "
        "Regenerate it."
    )


def test_every_component_has_a_version_and_a_purl(sbom):
    """A component without a version cannot be checked against an advisory feed."""
    weak = [
        c.get("name")
        for c in sbom.get("components", [])
        if not c.get("version") or not c.get("purl")
    ]
    assert not weak, f"components missing a version or purl: {weak[:10]}"


def test_it_is_not_older_than_the_dependency_list():
    """The SBOM must not predate the last change to the dependencies.

    Read from git history rather than a date inside the file. A date inside the
    file is one more field to forget. If pyproject.toml changed after the SBOM
    did, the SBOM describes a set of dependencies that is no longer declared.
    """
    sbom_at = _git("log", "-1", "--format=%ct", "--", "sbom.cdx.json")
    deps_at = _git("log", "-1", "--format=%ct", "--", "pyproject.toml")
    if not sbom_at or not deps_at:
        pytest.skip("no git history for one of the files")

    # Equal timestamps mean the same commit changed both, which is the coupling
    # the enforcement rule asks for.
    assert int(sbom_at) >= int(deps_at), (
        "pyproject.toml was changed more recently than sbom.cdx.json, so the "
        "SBOM is stale. Regenerate it and commit both together."
    )
