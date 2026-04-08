"""Tests for the intent routing config (single source of truth for intent classification)."""

import json

import pytest

from mgcp.intent_config import (
    CONFIG_VERSION,
    DEFAULT_INTENTS,
    IntentConfig,
    IntentDefinition,
    default_config,
    load_config,
    save_config,
)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect MGCP_DATA_DIR so tests don't touch the real ~/.mgcp."""
    monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Default config shape
# ---------------------------------------------------------------------------

class TestDefaultConfig:
    def test_includes_session_end_intent(self):
        """The session_end intent must exist — its absence is the bug
        that motivated this whole module."""
        names = {i.name for i in default_config().intents}
        assert "session_end" in names

    def test_includes_legacy_intents(self):
        names = {i.name for i in default_config().intents}
        for required in (
            "git_operation",
            "catalogue_dependency",
            "catalogue_security",
            "catalogue_decision",
            "catalogue_arch_note",
            "catalogue_convention",
            "task_start",
        ):
            assert required in names

    def test_session_end_has_keyword_gate(self):
        config = default_config()
        session_end = next(i for i in config.intents if i.name == "session_end")
        assert session_end.keyword_patterns, "session_end must have a hard keyword gate"
        assert session_end.gate_message
        assert "save_project_context" in session_end.gate_message
        assert "write_soliloquy" in session_end.gate_message

    def test_git_operation_has_keyword_gate(self):
        config = default_config()
        git = next(i for i in config.intents if i.name == "git_operation")
        assert git.keyword_patterns
        assert git.gate_message

    def test_catalogue_intents_have_no_keyword_gates(self):
        """Catalogue intents rely on LLM classification, not regex."""
        config = default_config()
        for intent in config.intents:
            if intent.name.startswith("catalogue_"):
                assert not intent.keyword_patterns, (
                    f"{intent.name} should not have keyword gates "
                    f"(too many false positives for catalogue terms)"
                )


# ---------------------------------------------------------------------------
# Tag mapping — verifies the bug fixes from the v2.1 hand-coded dict
# ---------------------------------------------------------------------------

class TestTagMapping:
    def test_session_management_stays_in_security(self):
        """``session-management`` is the OWASP web term for HTTP session
        token handling. It belongs in catalogue_security, not session_end.
        Don't confuse it with ``session-discipline``."""
        tag_map = default_config().tag_to_intent()
        assert tag_map.get("session-management") == "catalogue_security"

    def test_session_discipline_maps_to_session_end(self):
        """The v2.1 dict force-mapped these to task_start, hiding the
        missing session_end intent. Verify the fix."""
        tag_map = default_config().tag_to_intent()
        assert tag_map.get("session-discipline") == "session_end"
        assert tag_map.get("farewell") == "session_end"
        assert tag_map.get("save-context") == "session_end"

    def test_git_tags_map_to_git_operation(self):
        tag_map = default_config().tag_to_intent()
        assert tag_map.get("git") == "git_operation"
        assert tag_map.get("commits") == "git_operation"

    def test_tag_lookup_is_case_insensitive(self):
        tag_map = default_config().tag_to_intent()
        # Keys should already be lowercased by tag_to_intent()
        for key in tag_map:
            assert key == key.lower()


# ---------------------------------------------------------------------------
# Rendering — what the hooks consume
# ---------------------------------------------------------------------------

class TestRendering:
    def test_full_routing_includes_all_intents(self):
        config = default_config()
        rendered = config.render_full_routing()
        assert "<intent-routing>" in rendered
        assert "</intent-routing>" in rendered
        for intent in config.intents:
            assert intent.name in rendered

    def test_actions_block_includes_all_intents(self):
        config = default_config()
        rendered = config.render_actions()
        assert "<intent-actions>" in rendered
        assert "Multi-intent" in rendered
        for intent in config.intents:
            assert intent.name in rendered

    def test_terse_routing_includes_session_end(self):
        rendered = default_config().render_terse_routing()
        assert "session_end" in rendered
        assert "save_project_context" in rendered

    def test_terse_routing_has_none_fallback(self):
        rendered = default_config().render_terse_routing()
        assert "none" in rendered

    def test_keyword_gates_returned_for_gated_intents(self):
        gates = default_config().keyword_gates()
        intents_with_gates = {g["intent"] for g in gates}
        assert "git_operation" in intents_with_gates
        assert "session_end" in intents_with_gates

    def test_keyword_gate_shape(self):
        gates = default_config().keyword_gates()
        for gate in gates:
            assert "intent" in gate
            assert "patterns" in gate
            assert "message" in gate
            assert isinstance(gate["patterns"], list)
            assert gate["patterns"], f"{gate['intent']} has empty patterns"


# ---------------------------------------------------------------------------
# Persistence — load/save round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_load_creates_file_when_missing(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        assert not config_path.exists()
        config = load_config(config_path)
        assert config_path.exists()
        assert any(i.name == "session_end" for i in config.intents)

    def test_save_writes_rendered_cache(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        save_config(default_config(), config_path)
        with open(config_path) as f:
            data = json.load(f)
        assert data["version"] == CONFIG_VERSION
        assert "intents" in data
        assert "tag_to_intent" in data
        assert "rendered" in data
        rendered = data["rendered"]
        assert "session_init_routing" in rendered
        assert "session_init_actions" in rendered
        assert "dispatcher_routing" in rendered
        assert "keyword_gates" in rendered
        assert "<intent-routing>" in rendered["session_init_routing"]

    def test_round_trip(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        original = default_config()
        save_config(original, config_path)
        loaded = load_config(config_path)
        assert {i.name for i in loaded.intents} == {i.name for i in original.intents}
        assert loaded.tag_to_intent() == original.tag_to_intent()

    def test_load_falls_back_when_file_corrupt(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        config_path.write_text("not valid json {{{")
        config = load_config(config_path)
        # Returns defaults rather than crashing
        assert any(i.name == "session_end" for i in config.intents)

    def test_load_falls_back_when_intents_missing(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        config_path.write_text(json.dumps({"version": 1, "intents": []}))
        config = load_config(config_path)
        assert any(i.name == "session_end" for i in config.intents)


# ---------------------------------------------------------------------------
# Custom config support — proves the system supports growth (REM writeback)
# ---------------------------------------------------------------------------

class TestExtensibility:
    def test_can_add_custom_intent(self, tmp_config_dir):
        config_path = tmp_config_dir / "intent_config.json"
        custom = IntentConfig(
            version=CONFIG_VERSION,
            intents=[
                *DEFAULT_INTENTS,
                IntentDefinition(
                    name="phishing_research",
                    description="phishing campaign analysis, lure design",
                    action="search_catalogue and add_catalogue_item(item_type='security')",
                    tags=["phishing", "social-engineering"],
                ),
            ],
        )
        save_config(custom, config_path)
        loaded = load_config(config_path)
        names = {i.name for i in loaded.intents}
        assert "phishing_research" in names
        # Custom tags appear in the tag map
        assert loaded.tag_to_intent().get("phishing") == "phishing_research"
        # And in the rendered prompts
        assert "phishing_research" in loaded.render_full_routing()
        assert "phishing_research" in loaded.render_terse_routing()
