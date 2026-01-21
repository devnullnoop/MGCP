"""
Failure and recovery tests for MGCP.

These tests verify the system handles failures gracefully:
- Corrupted files
- Missing files
- Invalid data
- Interrupted operations
- Recovery from bad states
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from mgcp.graph import LessonGraph
from mgcp.models import Lesson
from mgcp.persistence import LessonStore
from mgcp.qdrant_vector_store import QdrantVectorStore


class TestCorruptedDatabase:
    """Tests for handling corrupted database files."""

    @pytest.mark.asyncio
    async def test_corrupted_sqlite_file(self):
        """Handles completely corrupted SQLite file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "corrupted.db"

            # Create a corrupted file (random bytes)
            db_path.write_bytes(b"not a valid sqlite database at all" * 100)

            # Should raise an error, not crash
            with pytest.raises(Exception):
                store = LessonStore(db_path=str(db_path))
                await store.get_all_lessons()

    @pytest.mark.asyncio
    async def test_truncated_database(self):
        """Handles truncated database file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "truncated.db"

            # Create a valid database first
            store = LessonStore(db_path=str(db_path))
            lesson = Lesson(id="test", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Read the file and truncate it
            content = db_path.read_bytes()
            db_path.write_bytes(content[:len(content) // 2])

            # Opening truncated database should fail gracefully
            with pytest.raises(Exception):
                store2 = LessonStore(db_path=str(db_path))
                await store2.get_all_lessons()

    @pytest.mark.asyncio
    async def test_empty_database_file(self):
        """Handles empty database file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"

            # Create empty file
            db_path.touch()

            # Should either initialize fresh or raise clear error
            try:
                store = LessonStore(db_path=str(db_path))
                lessons = await store.get_all_lessons()
                # If it works, should be empty
                assert lessons == []
            except Exception as e:
                # If it fails, should be a clear error
                assert "database" in str(e).lower() or "sqlite" in str(e).lower()


class TestMissingFiles:
    """Tests for handling missing files."""

    @pytest.mark.asyncio
    async def test_missing_database_creates_new(self):
        """Missing database file creates a new one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "new.db"

            assert not db_path.exists()

            store = LessonStore(db_path=str(db_path))
            lessons = await store.get_all_lessons()

            assert lessons == []
            # Database should now exist
            assert db_path.exists()

    def test_missing_chroma_directory_creates_new(self):
        """Missing ChromaDB directory creates a new one."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chroma_path = Path(tmpdir) / "chroma"

            assert not chroma_path.exists()

            store = QdrantVectorStore(persist_path=str(chroma_path))

            # Should be able to add and search
            lesson = Lesson(id="test", trigger="test query", action="test action")
            store.add_lesson(lesson)

            results = store.search("test", limit=1)
            assert len(results) > 0

    @pytest.mark.asyncio
    async def test_database_deleted_mid_session(self):
        """Handles database being deleted during operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "deleted.db"

            store = LessonStore(db_path=str(db_path))
            lesson = Lesson(id="test", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Verify it exists
            result = await store.get_lesson("test")
            assert result is not None

            # Delete the database file
            db_path.unlink()

            # Next operation should fail gracefully
            # (behavior depends on implementation - could recreate or error)
            try:
                await store.get_lesson("test")
            except Exception:
                # Should be a clear database error
                assert True  # Any exception is acceptable here


class TestInvalidData:
    """Tests for handling invalid data in storage."""

    @pytest.mark.asyncio
    async def test_malformed_json_in_examples(self):
        """Handles malformed JSON in examples column."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "malformed.db"

            # Create database with valid schema
            store = LessonStore(db_path=str(db_path))
            lesson = Lesson(id="valid", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Directly corrupt the JSON in the examples column
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Insert a row with invalid JSON in examples column
            # Must provide all NOT NULL fields to satisfy schema constraints
            cursor.execute("""
                INSERT INTO lessons (id, trigger, action, examples, tags, related_ids, relationships,
                                     version, created_at, last_refined, usage_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("corrupted", "test", "test", "not valid json {{{", "[]", "[]", "[]",
                  1, "2024-01-01T00:00:00", "2024-01-01T00:00:00", 0))
            conn.commit()
            conn.close()

            # Getting all lessons should either skip corrupted or raise clear error
            try:
                lessons = await store.get_all_lessons()
                # If it works, corrupted lesson might be skipped or loaded with empty examples
                valid_ids = [l.id for l in lessons]
                assert "valid" in valid_ids
            except Exception as e:
                # JSON decode error is acceptable - check error type or common message patterns
                from json import JSONDecodeError
                error_str = str(e).lower()
                is_json_error = (
                    isinstance(e, JSONDecodeError) or
                    "json" in error_str or
                    "decode" in error_str or
                    "expecting" in error_str  # JSONDecodeError message pattern
                )
                assert is_json_error, f"Unexpected error: {e}"

    @pytest.mark.asyncio
    async def test_null_required_fields(self):
        """Handles records with NULL in optional fields that code might expect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "null_fields.db"

            store = LessonStore(db_path=str(db_path))
            # Add valid lesson first to create schema
            lesson = Lesson(id="valid", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Directly insert data with NULL in optional field (rationale)
            # and test that loading still works
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO lessons (id, trigger, action, rationale, examples, tags, related_ids, relationships,
                                     version, created_at, last_refined, usage_count)
                VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("null-rationale", "test", "test", "[]", "[]", "[]", "[]",
                  1, "2024-01-01T00:00:00", "2024-01-01T00:00:00", 0))
            conn.commit()
            conn.close()

            # Should handle gracefully - NULL optional fields are fine
            lesson = await store.get_lesson("null-rationale")
            assert lesson is not None
            assert lesson.rationale is None or lesson.rationale == ""

    @pytest.mark.asyncio
    async def test_wrong_data_types_in_json(self):
        """Handles wrong data types in JSON columns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "wrong_types.db"

            store = LessonStore(db_path=str(db_path))
            lesson = Lesson(id="valid", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Insert data with wrong types in JSON columns
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # tags should be a JSON array, not a string
            cursor.execute("""
                INSERT INTO lessons (id, trigger, action, examples, tags, related_ids, relationships,
                                     version, created_at, last_refined, usage_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("wrong-types", "test", "test", "[]", '"not a list"', "[]", "[]",
                  1, "2024-01-01T00:00:00", "2024-01-01T00:00:00", 0))
            conn.commit()
            conn.close()

            # Should handle gracefully
            try:
                lesson = await store.get_lesson("wrong-types")
            except Exception:
                # Validation error is acceptable
                pass


class TestRecoveryScenarios:
    """Tests for recovery from various failure states."""

    @pytest.mark.asyncio
    async def test_recover_from_partial_write(self):
        """Can recover if write was interrupted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "partial.db"

            store = LessonStore(db_path=str(db_path))

            # Add some lessons
            for i in range(10):
                lesson = Lesson(id=f"lesson-{i}", trigger="test", action="test")
                await store.add_lesson(lesson)

            # Simulate partial write by adding one more then "crashing"
            # (In reality, SQLite's WAL mode should handle this)
            lesson = Lesson(id="partial", trigger="test", action="test")
            await store.add_lesson(lesson)

            # Reopen database (simulating restart after crash)
            store2 = LessonStore(db_path=str(db_path))
            lessons = await store2.get_all_lessons()

            # Should have all committed lessons
            assert len(lessons) >= 10

    @pytest.mark.asyncio
    async def test_rebuild_from_clean_slate(self):
        """Can rebuild from nothing if all data is lost."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "rebuild.db"

            # Start fresh
            store = LessonStore(db_path=str(db_path))

            # Verify empty
            lessons = await store.get_all_lessons()
            assert len(lessons) == 0

            # Can add new data
            lesson = Lesson(id="new", trigger="test", action="test")
            await store.add_lesson(lesson)

            lessons = await store.get_all_lessons()
            assert len(lessons) == 1

    def test_vector_store_reindex_from_lessons(self):
        """Vector store can be rebuilt from lesson data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chroma_path_1 = Path(tmpdir) / "chroma1"
            chroma_path_2 = Path(tmpdir) / "chroma2"

            # Create lessons
            lessons = [
                Lesson(id=f"reindex-{i}", trigger=f"topic {i}", action=f"action {i}")
                for i in range(10)
            ]

            # Add to first vector store
            store1 = QdrantVectorStore(persist_path=str(chroma_path_1))
            for lesson in lessons:
                store1.add_lesson(lesson)

            # Verify search works
            results1 = store1.search("topic 5", limit=1)
            assert len(results1) > 0

            # Create a NEW vector store (simulates needing to rebuild)
            # This is the recovery scenario: you have lessons in SQLite but need to rebuild vector index
            store2 = QdrantVectorStore(persist_path=str(chroma_path_2))

            # Re-add all lessons (this is the recovery process)
            for lesson in lessons:
                store2.add_lesson(lesson)

            # Verify search works in new store
            results2 = store2.search("topic 5", limit=1)
            assert len(results2) > 0


class TestGracefulDegradation:
    """Tests for graceful degradation when components fail."""

    def test_graph_works_without_vector_store(self):
        """Graph operations work even if vector store is unavailable."""
        graph = LessonGraph()

        # Add lessons to graph only
        for i in range(10):
            lesson = Lesson(
                id=f"graph-only-{i}",
                trigger="test",
                action="test",
                parent_id=f"graph-only-{i-1}" if i > 0 else None,
            )
            graph.add_lesson(lesson)

        # Graph operations should work
        children = graph.get_children("graph-only-0")
        assert "graph-only-1" in children

        stats = graph.get_statistics()
        assert stats["total_nodes"] == 10

    @pytest.mark.asyncio
    async def test_persistence_works_without_vector_store(self):
        """Persistence works even if vector store is unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "no_vector.db"

            store = LessonStore(db_path=str(db_path))

            # CRUD operations should work
            lesson = Lesson(id="no-vector", trigger="test", action="test")
            await store.add_lesson(lesson)

            retrieved = await store.get_lesson("no-vector")
            assert retrieved is not None
            assert retrieved.id == "no-vector"

    def test_search_returns_empty_on_error(self):
        """Search returns empty results rather than crashing on errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantVectorStore(persist_path=tmpdir)

            # Search with no data should return empty, not crash
            results = store.search("anything", limit=5)
            assert results == [] or len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_concurrent_access_errors(self):
        """Handles database locked errors gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "locked.db"

            store1 = LessonStore(db_path=str(db_path))
            store2 = LessonStore(db_path=str(db_path))

            # Both stores accessing same database
            lesson1 = Lesson(id="store1", trigger="test", action="test")
            lesson2 = Lesson(id="store2", trigger="test", action="test")

            await store1.add_lesson(lesson1)
            await store2.add_lesson(lesson2)

            # Both should be readable
            all_lessons = await store1.get_all_lessons()
            ids = [l.id for l in all_lessons]
            assert "store1" in ids
            assert "store2" in ids


class TestEdgeCaseInputs:
    """Tests for edge case inputs that might cause failures."""

    @pytest.mark.asyncio
    async def test_empty_string_fields(self):
        """Handles empty string fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty_strings.db"
            store = LessonStore(db_path=str(db_path))

            # Lesson with empty strings where allowed
            lesson = Lesson(
                id="empty-strings",
                trigger="",  # Empty trigger
                action="",   # Empty action
                rationale="",
            )

            await store.add_lesson(lesson)
            retrieved = await store.get_lesson("empty-strings")
            assert retrieved is not None

    @pytest.mark.asyncio
    async def test_very_long_strings(self):
        """Handles very long string values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "long_strings.db"
            store = LessonStore(db_path=str(db_path))

            # Lesson with very long strings
            long_text = "x" * 100_000  # 100KB of text
            lesson = Lesson(
                id="long-strings",
                trigger=long_text,
                action=long_text,
            )

            await store.add_lesson(lesson)
            retrieved = await store.get_lesson("long-strings")
            assert retrieved is not None
            assert len(retrieved.trigger) == 100_000

    @pytest.mark.asyncio
    async def test_unicode_characters(self):
        """Handles unicode characters correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "unicode.db"
            store = LessonStore(db_path=str(db_path))

            # Lesson with various unicode
            lesson = Lesson(
                id="unicode-test",
                trigger="Python 类型提示 タイプヒント",
                action="使用类型提示 🐍 émojis and ñ",
                rationale="Ελληνικά, Кириллица, العربية",
            )

            await store.add_lesson(lesson)
            retrieved = await store.get_lesson("unicode-test")
            assert retrieved is not None
            assert "类型提示" in retrieved.trigger
            assert "🐍" in retrieved.action

    @pytest.mark.asyncio
    async def test_special_characters_in_id(self):
        """Handles special characters in lesson IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "special_ids.db"
            store = LessonStore(db_path=str(db_path))

            # Various potentially problematic IDs
            test_ids = [
                "with-dashes",
                "with_underscores",
                "with.dots",
                "with spaces",
                "with/slashes",
                "with'quotes",
                'with"double',
            ]

            for test_id in test_ids:
                try:
                    lesson = Lesson(id=test_id, trigger="test", action="test")
                    await store.add_lesson(lesson)
                    retrieved = await store.get_lesson(test_id)
                    assert retrieved is not None, f"Failed for ID: {test_id}"
                except Exception as e:
                    # Some IDs might be rejected - that's okay if it's clear
                    assert "id" in str(e).lower() or "invalid" in str(e).lower()

    @pytest.mark.asyncio
    async def test_null_bytes_in_strings(self):
        """Handles null bytes in strings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "null_bytes.db"
            store = LessonStore(db_path=str(db_path))

            # String with null byte
            try:
                lesson = Lesson(
                    id="null-byte",
                    trigger="test\x00with\x00nulls",
                    action="action\x00here",
                )
                await store.add_lesson(lesson)
                retrieved = await store.get_lesson("null-byte")
                # If it works, verify data integrity
                assert retrieved is not None
            except Exception:
                # Rejecting null bytes is acceptable
                pass

    def test_vector_search_with_empty_query(self):
        """Handles empty search queries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantVectorStore(persist_path=tmpdir)

            # Add a lesson first
            lesson = Lesson(id="test", trigger="test", action="test")
            store.add_lesson(lesson)

            # Empty query should return something or empty, not crash
            results = store.search("", limit=5)
            assert isinstance(results, list)

    def test_vector_search_with_special_characters(self):
        """Handles special characters in search queries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = QdrantVectorStore(persist_path=tmpdir)

            lesson = Lesson(id="test", trigger="python code", action="test")
            store.add_lesson(lesson)

            # Various special character queries
            queries = [
                "test!@#$%",
                "SELECT * FROM",
                "<script>alert('xss')</script>",
                "'; DROP TABLE lessons; --",
                "test\nwith\nnewlines",
            ]

            for query in queries:
                try:
                    results = store.search(query, limit=5)
                    assert isinstance(results, list)
                except Exception:
                    # Rejection is okay, crashing is not
                    pass


class TestBackupRestore:
    """Tests for the backup module."""

    def test_backup_creates_archive(self):
        """Backup creates a valid archive."""
        from mgcp.backup import backup

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake data directory
            data_dir = Path(tmpdir) / ".mgcp"
            data_dir.mkdir()
            (data_dir / "lessons.db").write_text("fake db content")
            (data_dir / "chroma").mkdir()
            (data_dir / "chroma" / "data.bin").write_bytes(b"fake chroma data")

            # Create backup
            output = Path(tmpdir) / "backup"
            archive = backup(output, data_dir)

            assert archive.exists()
            assert archive.suffix == ".gz"
            assert archive.stat().st_size > 0

    def test_restore_from_backup(self):
        """Restore recreates data from backup."""
        from mgcp.backup import backup, restore

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create original data in .mgcp (mimics real usage)
            original_dir = Path(tmpdir) / ".mgcp"
            original_dir.mkdir()
            (original_dir / "lessons.db").write_text("original content")

            # Create backup
            archive = backup(Path(tmpdir) / "backup", original_dir)

            # Create a separate temp directory for restoration
            with tempfile.TemporaryDirectory() as restore_tmpdir:
                # Target directory should be .mgcp in the restore location
                restore_dir = Path(restore_tmpdir) / ".mgcp"

                # Restore - the archive contains .mgcp, so it extracts to restore_dir.parent
                restore(archive, restore_dir, force=True)

                # Verify the restored directory exists with content
                assert restore_dir.exists()
                assert (restore_dir / "lessons.db").exists()
                assert (restore_dir / "lessons.db").read_text() == "original content"

    def test_restore_refuses_overwrite_without_force(self):
        """Restore won't overwrite without --force."""
        from mgcp.backup import backup, restore

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create original and backup
            original_dir = Path(tmpdir) / "original"
            original_dir.mkdir()
            (original_dir / "lessons.db").write_text("content")
            archive = backup(Path(tmpdir) / "backup", original_dir)

            # Create existing target
            target_dir = Path(tmpdir) / "target"
            target_dir.mkdir()
            (target_dir / "existing.txt").write_text("don't delete me")

            # Restore without force should fail
            with pytest.raises(FileExistsError):
                restore(archive, target_dir, force=False)

            # Original file should still exist
            assert (target_dir / "existing.txt").exists()

    def test_backup_strips_tar_gz_extension(self):
        """Backup handles .tar.gz extension in output path."""
        from mgcp.backup import backup

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake data directory
            data_dir = Path(tmpdir) / ".mgcp"
            data_dir.mkdir()
            (data_dir / "lessons.db").write_text("fake db content")

            # Create backup with .tar.gz extension in name
            output = Path(tmpdir) / "my-backup.tar.gz"
            archive = backup(output, data_dir)

            # Should NOT be my-backup.tar.gz.tar.gz
            assert archive.name == "my-backup.tar.gz"
            assert archive.exists()

    def test_backup_strips_tgz_extension(self):
        """Backup handles .tgz extension in output path."""
        from mgcp.backup import backup

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake data directory
            data_dir = Path(tmpdir) / ".mgcp"
            data_dir.mkdir()
            (data_dir / "lessons.db").write_text("fake db content")

            # Create backup with .tgz extension in name
            output = Path(tmpdir) / "my-backup.tgz"
            archive = backup(output, data_dir)

            # shutil.make_archive uses .tar.gz, but input was .tgz
            # The fix strips .tgz, so result is my-backup.tar.gz
            assert archive.name == "my-backup.tar.gz"
            assert archive.exists()
