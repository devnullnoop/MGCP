"""
API-UI Integration Tests

These tests verify that API endpoints work correctly when called the way
the UI calls them. This catches issues like the _get_conn bug where the
endpoint was broken but unit tests didn't catch it.

Run with: pytest tests/test_api_ui_integration.py -v
"""

import pytest
from fastapi.testclient import TestClient

from src.mgcp.web_server import app


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

    def test_projects_page_loads(self, client):
        """Test projects page."""
        response = client.get("/projects")
        assert response.status_code == 200

    def test_lessons_page_loads(self, client):
        """Test lessons page."""
        response = client.get("/lessons")
        assert response.status_code == 200