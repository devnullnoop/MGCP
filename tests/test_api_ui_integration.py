"""
API-UI Integration Tests

These tests verify that API endpoints work correctly when called the way
the UI calls them. This catches issues like the _get_conn bug where the
endpoint was broken but unit tests didn't catch it.

Run with: pytest tests/test_api_ui_integration.py -v
"""

import pytest
from fastapi.testclient import TestClient

# The installed package, not `src.mgcp`: without src/__init__.py the two
# import paths are separate module objects, so `src.mgcp.web_server` would
# build a second LessonStore and a second QdrantVectorStore on the same
# files as the rest of the suite -- the Qdrant "already accessed by another
# instance" failure conftest.py exists to prevent -- and would not be the
# app that mgcp.web_server:main actually serves.
from mgcp.web_server import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestProjectCRUD:
    """Test project CRUD operations as the UI performs them."""

    def test_create_project(self, client):
        """Test creating a project via API (as UI does)."""
        response = client.post(
            "/api/projects",
            json={"project_path": "/tmp/test-project", "project_name": "Test Project"},
        )
        assert response.status_code == 200
        data = response.json()
        # UI checks for error key
        assert "error" not in data or data.get("error") == "Project already exists"
        if "error" not in data:
            assert "project_id" in data

    def test_get_all_projects(self, client):
        """Test fetching all projects (as UI does on load)."""
        response = client.get("/api/projects")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_delete_project(self, client):
        """Test deleting a project - THIS IS THE BUG WE FOUND.

        The UI calls DELETE /api/projects/{project_id} and expects:
        - Success: {"deleted": project_id}
        - Not found: {"error": "Project not found"}
        - Failure: null (from fetchAPI catching exception)

        The original bug was that the endpoint called store._get_conn()
        which doesn't exist, causing an AttributeError that made
        the request fail silently.
        """
        # First create a project to delete
        create_response = client.post(
            "/api/projects",
            json={
                "project_path": "/tmp/delete-test-project",
                "project_name": "Delete Test",
            },
        )
        create_data = create_response.json()

        # Handle case where project already exists
        if "error" in create_data and create_data["error"] == "Project already exists":
            project_id = create_data["project_id"]
        else:
            project_id = create_data["project_id"]

        # Now delete it - THIS IS THE CRITICAL TEST
        delete_response = client.delete(f"/api/projects/{project_id}")
        assert delete_response.status_code == 200
        delete_data = delete_response.json()

        # UI checks: result !== null && !result.error
        assert delete_data is not None, "Delete should not return null"
        assert "deleted" in delete_data or "error" in delete_data, (
            "Response must have 'deleted' or 'error' key"
        )

        if "deleted" in delete_data:
            assert delete_data["deleted"] == project_id

    def test_delete_nonexistent_project(self, client):
        """Test deleting a project that doesn't exist."""
        response = client.delete("/api/projects/nonexistent123")
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"] == "Project not found"


class TestLessonCRUD:
    """Test lesson CRUD operations as the UI performs them."""

    def test_get_all_lessons(self, client):
        """Test fetching all lessons."""
        response = client.get("/api/lessons")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_single_lesson(self, client):
        """Test fetching a single lesson."""
        # First get all lessons
        all_response = client.get("/api/lessons")
        lessons = all_response.json()

        if lessons:
            lesson_id = lessons[0]["id"]
            response = client.get(f"/api/lessons/{lesson_id}")
            assert response.status_code == 200
            data = response.json()
            assert data is not None
            assert data["id"] == lesson_id


class TestCatalogueOperations:
    """Test catalogue operations."""

    def test_get_catalogue(self, client):
        """Test getting project catalogue."""
        # First ensure we have a project
        client.post(
            "/api/projects",
            json={"project_path": "/tmp/catalogue-test", "project_name": "Catalogue Test"},
        )

        # Get all projects and find ours
        projects = client.get("/api/projects").json()
        if projects:
            project_id = projects[0]["project_id"]
            response = client.get(f"/api/projects/{project_id}/catalogue")
            assert response.status_code == 200
            data = response.json()
            # Should have catalogue structure or error
            assert "error" in data or isinstance(data, dict)


class TestHealthAndDocs:
    """Test health check and documentation endpoints."""

    def test_health_check(self, client):
        """Test health endpoint."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_openapi_docs(self, client):
        """Test that OpenAPI docs are available."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_redoc(self, client):
        """Test that ReDoc is available."""
        response = client.get("/redoc")
        assert response.status_code == 200


class TestUIPages:
    """Test that UI pages load."""

    def test_dashboard_loads(self, client):
        """Test dashboard page."""
        response = client.get("/")
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "path", ["/projects", "/lessons", "/signal", "/enforcement", "/rem", "/journal"]
    )
    def test_view_paths_serve_the_app(self, client, path):
        """Every view path serves the one document; the hash router picks the view.

        The old per-page routes are gone, but /lessons and /projects are in
        people's bookmarks, so they fall through to the app rather than 404.
        """
        response = client.get(path)
        assert response.status_code == 200
        assert "MGCP Instrument" in response.text

    @pytest.mark.parametrize("path", ["/api/nope", "/openapi.json"])
    def test_reserved_paths_do_not_fall_through(self, client, path):
        """The catch-all must not swallow the API or the schema."""
        response = client.get(path)
        assert response.status_code != 200 or "MGCP Instrument" not in response.text


class TestVectorStoreIsLazy:
    """The dashboard must not open Qdrant to serve read-only views.

    Local-mode Qdrant allows one client per path, so an eager open meant the
    web server could not start at all while an MCP server held the lock —
    although none of the analytics views need vectors. They read lessons.db,
    telemetry.db and gate_audit.jsonl; only lesson writes touch Qdrant.
    """

    def test_startup_does_not_open_the_vector_store(self, client):
        import mgcp.web_server as ws

        ws.vector_store = None
        response = client.get("/api/health")
        assert response.status_code == 200
        assert ws.vector_store is None, (
            "the vector store was opened during startup/health, which is what "
            "stopped the dashboard running beside the MCP server"
        )
        assert "lazy" in response.json()["vector_store"]

    @pytest.mark.parametrize("path", ["/api/signal", "/api/gate-audit", "/api/rem/state"])
    def test_analytics_views_never_open_the_vector_store(self, client, path):
        import mgcp.web_server as ws

        ws.vector_store = None
        response = client.get(path)
        assert response.status_code == 200
        assert ws.vector_store is None, f"{path} opened Qdrant; it reads SQLite only"


class TestQueryConcentration:
    """The raw query count and the per-question count must be able to disagree.

    Every figure under `retrieval` counts events, and the hooks issue some of
    those events. In the operator's own store the git gate's mandated
    `query_lessons('git commit')` is 521 of 1,161 recorded queries, and one
    lesson wins 517 of them at a nearly constant score, which pulled the
    published median from 0.608 to 0.691. These tests hold the deflating twin
    in place, because a metric that cannot disagree with the raw figure is
    decoration.
    """

    @staticmethod
    def _pairs(rows):
        """Minimal pair records: (query text, best matched score)."""
        return [{"query": q, "top": s} for q, s in rows]

    def test_one_repeated_query_does_not_decide_the_per_question_median(self):
        from mgcp.web_server import _query_concentration

        # 20 copies of a high-scoring reflex, 5 distinct lower-scoring questions.
        rows = [("git commit", 0.69)] * 20 + [
            ("why did the cache miss", 0.50),
            ("how do I rotate the key", 0.52),
            ("where is the retry budget set", 0.48),
            ("what owns the lock file", 0.51),
            ("which step writes the index", 0.49),
        ]
        c = _query_concentration(self._pairs(rows))

        assert c["queries"] == 25
        assert c["distinct_queries"] == 6
        assert c["asked_once"] == 5
        assert c["top_query_share"] == pytest.approx(20 / 25)
        # The raw median is the reflex's score. The per-question median is not.
        assert c["top1_median_per_question"] < 0.60, (
            "the repeated query still dominates the deduplicated figure"
        )

    def test_the_two_figures_agree_when_nothing_repeats(self):
        from mgcp.web_server import _query_concentration

        rows = [(f"question {i}", 0.60) for i in range(8)]
        c = _query_concentration(self._pairs(rows))

        assert c["distinct_queries"] == c["queries"] == 8
        assert c["repeat_share"] == 0.0
        assert c["most_repeated"] == []  # nothing to report, so nothing claimed
        assert c["top1_mean_per_question"] == pytest.approx(0.60)

    def test_a_repeated_query_asked_at_different_scores_averages_once(self):
        """A question asked twice gets one vote, not two."""
        from mgcp.web_server import _query_concentration

        c = _query_concentration(
            self._pairs([("same question", 0.40), ("same question", 0.80)])
        )

        assert c["distinct_queries"] == 1
        assert c["scored_questions"] == 1
        assert c["top1_mean_per_question"] == pytest.approx(0.60)

    def test_unscored_queries_do_not_count_as_zero(self):
        """A query that matched nothing has no score, which is not a score of 0."""
        from mgcp.web_server import _query_concentration

        c = _query_concentration(
            self._pairs([("found nothing", None), ("found something", 0.70)])
        )

        assert c["queries"] == 2
        assert c["distinct_queries"] == 2
        assert c["scored_questions"] == 1
        assert c["top1_mean_per_question"] == pytest.approx(0.70)

    def test_empty_history_reports_nothing_rather_than_dividing_by_zero(self):
        from mgcp.web_server import _query_concentration

        c = _query_concentration([])

        assert c["queries"] == 0
        assert c["distinct_queries"] == 0
        assert c["top_query_share"] == 0.0
        assert c["top1_median_per_question"] is None


class TestCodeSize:
    """`/api/code-size`: net source growth per commit.

    The git reads are exercised against a repository built here, because the
    operator's own store is the wrong place to assert a number from. The
    endpoint test covers the shape and the denominator only.
    """

    GIT_ENV = {
        "PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }

    def _repo(self, tmp_path):
        """Three commits: +10 source, then -9/+1 source, then one test file."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**self.GIT_ENV, "HOME": str(tmp_path)}

        def run(*args):
            subprocess.run(["git", "-C", str(repo), *args], env=env, check=True)

        def commit(message):
            run("add", "-A")
            run("commit", "-qm", message)

        run("init", "-q")
        (repo / "src").mkdir()
        (repo / "src" / "a.py").write_text("x\n" * 10)
        commit("add ten")
        (repo / "src" / "a.py").write_text("y\n")
        commit("cut nine")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("t\n" * 30)
        commit("add a test")
        return repo

    def test_net_is_added_minus_removed_per_commit(self, tmp_path):
        from mgcp.web_server import _commit_sizes

        rows = _commit_sizes(self._repo(tmp_path), ["tests/**"], 10)
        by_subject = {r["subject"]: r for r in rows}
        assert by_subject["add ten"]["net"] == 10
        # One line replaces ten, so this reads below the line, which counting
        # additions alone cannot express.
        assert by_subject["cut nine"]["added"] == 1
        assert by_subject["cut nine"]["removed"] == 10
        assert by_subject["cut nine"]["net"] == -9

    def test_an_excluded_only_commit_is_absent(self, tmp_path):
        """It changed no source, so it is not a row of zeros."""
        from mgcp.web_server import _commit_sizes

        rows = _commit_sizes(self._repo(tmp_path), ["tests/**"], 10)
        assert "add a test" not in {r["subject"] for r in rows}
        assert len(rows) == 2

    def test_without_the_exclusion_that_commit_is_measured(self, tmp_path):
        """The pair above only means something if the path is otherwise seen."""
        from mgcp.web_server import _commit_sizes

        rows = _commit_sizes(self._repo(tmp_path), [], 10)
        by_subject = {r["subject"]: r for r in rows}
        assert by_subject["add a test"]["net"] == 30

    def test_a_directory_that_is_not_a_repository_measures_nothing(self, tmp_path):
        from mgcp.web_server import _commit_sizes

        plain = tmp_path / "plain"
        plain.mkdir()
        assert _commit_sizes(plain, [], 10) == []

    def test_a_binary_file_has_no_line_count(self, tmp_path):
        """numstat prints "-" for both counts, which is not zero."""
        from mgcp.web_server import _numstat_totals

        assert _numstat_totals("-\t-\timage.png\n4\t1\tsrc/a.py") == (4, 1, 1)

    def test_the_exclusions_come_from_the_commit_budget_rule(self):
        """The chart and the gate have to measure the same paths."""
        from mgcp.enforcement import default_config
        from mgcp.web_server import _budget_exclude_globs

        shipped = [p.exclude_globs for r in default_config().rules
                   for p in r.preconditions if p.type == "diff_budget"]
        assert shipped, "no shipped rule carries a diff_budget precondition"
        assert _budget_exclude_globs() in shipped

    def test_the_endpoint_reports_its_denominator(self, client):
        """`measured` without `tracked` cannot say whether it covers everything."""
        response = client.get("/api/code-size?limit=5")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"excluded", "tracked", "measured", "projects"}
        assert body["measured"] == len(body["projects"])
        assert body["measured"] <= body["tracked"]
        for project in body["projects"]:
            assert set(project) == {"project", "project_id", "path",
                                    "commits", "totals"}
            assert project["totals"]["net"] == (
                project["totals"]["added"] - project["totals"]["removed"])
            assert len(project["commits"]) <= 5
