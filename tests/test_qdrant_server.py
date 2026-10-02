"""Tests for the locally installed Qdrant server that makes MGCP multi-session.

Embedded Qdrant permits one client per directory, so more than one session needs
a server. Before this, the only route to one was a Docker container the user
installed themselves and `mgcp-init` never mentioned — which is why v3 shipped
"multi-session" that nobody could reach.

The platform mapping is tested for every published target including Windows,
because the machine this was written on is macOS/arm64 and a mapping that is only
exercised where it was written is how a Windows user gets a crash instead of a
download.
"""

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from mgcp import config, qdrant_server
from mgcp.qdrant_server import ASSETS, QDRANT_VERSION, QdrantServerError


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every MGCP path at a throwaway directory."""
    monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MGCP_QDRANT_URL", raising=False)
    return tmp_path


class TestPlatformMapping:
    """Every platform Qdrant publishes for, not just the one this was written on."""

    @pytest.mark.parametrize(
        "system,machine,expected",
        [
            ("Darwin", "arm64", "qdrant-aarch64-apple-darwin.tar.gz"),
            ("Darwin", "x86_64", "qdrant-x86_64-apple-darwin.tar.gz"),
            ("Windows", "AMD64", "qdrant-x86_64-pc-windows-msvc.zip"),
            ("Windows", "x86_64", "qdrant-x86_64-pc-windows-msvc.zip"),
            ("Linux", "x86_64", "qdrant-x86_64-unknown-linux-gnu.tar.gz"),
            ("Linux", "aarch64", "qdrant-aarch64-unknown-linux-musl.tar.gz"),
            # Same silicon, different spelling per OS. Normalising is the point.
            ("Linux", "arm64", "qdrant-aarch64-unknown-linux-musl.tar.gz"),
        ],
    )
    def test_each_published_target_resolves(self, system, machine, expected, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: system)
        monkeypatch.setattr("platform.machine", lambda: machine)
        asset, sha = qdrant_server.asset_for_platform()
        assert asset == expected
        assert len(sha) == 64, "every asset needs a recorded checksum"

    def test_windows_binary_is_named_exe(self, monkeypatch, data_dir):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setattr("platform.machine", lambda: "AMD64")
        assert qdrant_server.binary_path().name == "qdrant.exe"

    def test_unix_binary_has_no_extension(self, monkeypatch, data_dir):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        assert qdrant_server.binary_path().name == "qdrant"

    def test_unsupported_platform_says_what_to_do(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "SunOS")
        monkeypatch.setattr("platform.machine", lambda: "sparc")
        with pytest.raises(QdrantServerError) as exc:
            qdrant_server.asset_for_platform()
        # Not just "unsupported": the operator needs the way out.
        assert "qdrant_url" in str(exc.value)
        assert "darwin/arm64" in str(exc.value)

    def test_every_asset_has_a_distinct_checksum(self):
        shas = [sha for _name, sha in ASSETS.values()]
        assert len(shas) == len(set(shas)), "a copied checksum would verify the wrong file"


class TestVersionPin:
    """The client/server version spread is a shipped-docs bug waiting to happen."""

    def test_server_version_matches_the_client_pin(self):
        """qdrant-client warns when the spread exceeds one minor.

        v3 shipped `qdrant-client>=1.12.0` while telling users to run the latest
        server image, so every connection warned "incompatible". Pinning both here
        means a bump to one side fails this test rather than reaching a user.
        """
        pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        pin = next(
            line for line in pyproject.splitlines() if "qdrant-client>=" in line
        )
        client_version = pin.split("qdrant-client>=")[1].split('"')[0]
        client_major, client_minor = (int(p) for p in client_version.split(".")[:2])
        server_major, server_minor = (int(p) for p in QDRANT_VERSION.split(".")[:2])
        assert server_major == client_major, (
            f"server {QDRANT_VERSION} and client {client_version} differ in major version; "
            "the client refuses that outright"
        )
        assert abs(server_minor - client_minor) <= 1, (
            f"server {QDRANT_VERSION} vs client {client_version}: more than one minor "
            "apart, which warns on every connection"
        )


class TestInstallVerification:
    """A downloaded binary is verified, or not installed."""

    def _tar_with(self, names: list[str]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in names:
                data = b"#!/bin/sh\necho qdrant 1.19.1\n"
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_checksum_mismatch_refuses_to_install(self, data_dir, monkeypatch):
        archive_bytes = self._tar_with(["qdrant"])

        def fake_download(url, dest, progress=True):
            dest.write_bytes(archive_bytes)

        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        monkeypatch.setattr(qdrant_server, "_download", fake_download)

        with pytest.raises(QdrantServerError) as exc:
            qdrant_server.install(progress=False)
        assert "Checksum mismatch" in str(exc.value)
        assert not qdrant_server.binary_path().exists(), "a bad archive must install nothing"

    def test_good_checksum_installs_and_is_executable(self, data_dir, monkeypatch):
        archive_bytes = self._tar_with(["qdrant"])
        digest = hashlib.sha256(archive_bytes).hexdigest()

        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        monkeypatch.setitem(
            qdrant_server.ASSETS, ("darwin", "arm64"), ("qdrant-fake.tar.gz", digest)
        )
        monkeypatch.setattr(
            qdrant_server, "_download", lambda url, dest, progress=True: dest.write_bytes(archive_bytes)
        )
        # The shipped shell stub prints the expected version, so the post-install
        # check passes without a 27 MB download.
        installed = qdrant_server.install(progress=False)
        assert installed.exists()
        assert installed.stat().st_mode & 0o111, "binary must be executable"

    def test_archive_without_the_binary_is_rejected(self, data_dir, monkeypatch):
        archive_bytes = self._tar_with(["README.md"])
        digest = hashlib.sha256(archive_bytes).hexdigest()
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")
        monkeypatch.setitem(
            qdrant_server.ASSETS, ("darwin", "arm64"), ("qdrant-fake.tar.gz", digest)
        )
        monkeypatch.setattr(
            qdrant_server, "_download", lambda url, dest, progress=True: dest.write_bytes(archive_bytes)
        )
        with pytest.raises(QdrantServerError) as exc:
            qdrant_server.install(progress=False)
        assert "does not contain qdrant" in str(exc.value)

    def test_zip_extraction_takes_the_exe(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("qdrant.exe", b"binary")
        archive = tmp_path / "qdrant-win.zip"
        archive.write_bytes(buf.getvalue())
        extracted = qdrant_server._extract_binary(archive, tmp_path / "bin")
        assert extracted.name == "qdrant.exe"
        assert extracted.read_bytes() == b"binary"


class TestUrlResolution:
    """How a session decides which store it is talking to."""

    def test_embedded_when_nothing_is_configured(self, data_dir):
        from mgcp.qdrant_vector_store import get_qdrant_url

        assert get_qdrant_url() is None

    def test_config_file_selects_server_mode(self, data_dir):
        """The file, not the env var, is what makes multi-session reachable.

        An MCP server's environment comes from the LLM client that spawned it, so
        `export MGCP_QDRANT_URL=...` in a shell never reaches it.
        """
        from mgcp.qdrant_vector_store import get_qdrant_url

        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        assert get_qdrant_url() == "http://127.0.0.1:6333"

    def test_env_var_wins_over_the_config_file(self, data_dir, monkeypatch):
        from mgcp.qdrant_vector_store import get_qdrant_url

        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        monkeypatch.setenv("MGCP_QDRANT_URL", "http://elsewhere:6333")
        assert get_qdrant_url() == "http://elsewhere:6333"

    def test_blank_config_value_means_embedded(self, data_dir):
        from mgcp.qdrant_vector_store import get_qdrant_url

        config.set_value("qdrant_url", "   ")
        assert get_qdrant_url() is None

    def test_corrupt_config_raises_rather_than_guessing(self, data_dir):
        """Silently using embedded while the operator believes they are on the
        server is two sessions writing to two stores with nothing saying so."""
        config.config_path().write_text("{not json")
        with pytest.raises(ValueError) as exc:
            config.load_config()
        assert "not valid JSON" in str(exc.value)

    def test_client_args_follow_the_config(self, data_dir):
        from mgcp.qdrant_vector_store import qdrant_client_args

        assert "path" in qdrant_client_args("/tmp/x")
        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        args = qdrant_client_args("/tmp/x")
        assert args == {"url": "http://127.0.0.1:6333"}


class TestAutostartGuards:
    """When MGCP may and may not start a server behind the operator's back."""

    def test_no_config_means_no_autostart(self, data_dir):
        assert qdrant_server.ensure_running_if_configured() is False

    def test_never_starts_somebody_elses_server(self, data_dir, monkeypatch):
        """A remote or non-default URL is not ours to supervise."""
        config.set_value("qdrant_url", "http://vector-host.internal:6333")
        monkeypatch.setattr(qdrant_server, "is_answering", lambda *a, **k: False)
        called = []
        monkeypatch.setattr(qdrant_server, "start", lambda **k: called.append(k))
        assert qdrant_server.ensure_running_if_configured() is False
        assert called == []

    def test_autostart_can_be_disabled(self, data_dir, monkeypatch):
        config.set_value("qdrant_url", qdrant_server.default_url())
        monkeypatch.setenv("MGCP_QDRANT_AUTOSTART", "0")
        monkeypatch.setattr(qdrant_server, "is_answering", lambda *a, **k: False)
        monkeypatch.setattr(qdrant_server, "installed_version", lambda: QDRANT_VERSION)
        called = []
        monkeypatch.setattr(qdrant_server, "start", lambda **k: called.append(k))
        assert qdrant_server.ensure_running_if_configured() is False
        assert called == []

    def test_starts_our_own_server_when_it_is_down(self, data_dir, monkeypatch):
        config.set_value("qdrant_url", qdrant_server.default_url())
        monkeypatch.setattr(qdrant_server, "installed_version", lambda: QDRANT_VERSION)
        answers = iter([False, True])
        monkeypatch.setattr(qdrant_server, "is_answering", lambda *a, **k: next(answers, True))
        started = []
        monkeypatch.setattr(
            qdrant_server, "start", lambda **k: started.append(k) or qdrant_server.default_url()
        )
        assert qdrant_server.ensure_running_if_configured() is True
        assert started, "a configured local server that is down should be started"

    def test_a_failed_start_degrades_instead_of_raising(self, data_dir, monkeypatch):
        """The caller has a "vector store unavailable" path that names the remedy;
        an exception here would bypass it and take out SQLite-only tools too."""
        config.set_value("qdrant_url", qdrant_server.default_url())
        monkeypatch.setattr(qdrant_server, "installed_version", lambda: QDRANT_VERSION)
        monkeypatch.setattr(qdrant_server, "is_answering", lambda *a, **k: False)

        def boom(**_kw):
            raise QdrantServerError("no binary")

        monkeypatch.setattr(qdrant_server, "start", boom)
        assert qdrant_server.ensure_running_if_configured() is False


class TestPaths:
    """Everything under MGCP_DATA_DIR, like every other store."""

    def test_all_paths_honour_the_data_dir(self, data_dir):
        for path in (
            qdrant_server.binary_path(),
            qdrant_server.storage_dir(),
            qdrant_server.pid_file(),
            qdrant_server.log_file(),
            config.config_path(),
        ):
            assert str(path).startswith(str(data_dir)), path

    def test_server_storage_is_not_the_embedded_directory(self, data_dir):
        """Two processes owning the same files with different assumptions."""
        from mgcp.qdrant_vector_store import get_default_qdrant_path

        assert qdrant_server.storage_dir() != Path(get_default_qdrant_path())

    def test_status_reports_mode_without_a_server(self, data_dir, monkeypatch):
        """`answering` probes 127.0.0.1:6333, which is machine-global state.

        Asserting it directly made this test pass only on a machine with nothing
        on that port — it broke the moment a real server was running, which is
        the configuration the feature exists for. The probe is stubbed so the
        test is about what `status` reports, not about what happens to be up.
        """
        monkeypatch.setattr(qdrant_server, "is_answering", lambda *a, **k: False)
        status = qdrant_server.status()
        assert status["mode"] == "embedded (single session)"
        assert status["installed_version"] is None
        assert status["answering"] is False

    def test_status_reports_a_broken_config_instead_of_raising(self, data_dir):
        config.config_path().write_text("{bad")
        status = qdrant_server.status()
        assert status["config_error"] is not None
        assert "not valid JSON" in status["config_error"]


class TestConfigFile:
    def test_missing_file_is_empty_not_an_error(self, data_dir):
        assert config.load_config() == {}

    def test_set_value_preserves_other_keys(self, data_dir):
        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        config.set_value("something_else", 42)
        loaded = config.load_config()
        assert loaded == {"qdrant_url": "http://127.0.0.1:6333", "something_else": 42}

    def test_set_none_removes_the_key(self, data_dir):
        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        config.set_value("qdrant_url", None)
        assert "qdrant_url" not in config.load_config()

    def test_write_is_atomic(self, data_dir):
        """The hooks read this file on every tool call; a truncated read must be
        impossible, which is why enforcement_rules.json is written the same way."""
        config.set_value("qdrant_url", "http://127.0.0.1:6333")
        assert not list(data_dir.glob("*.tmp")), "temp file left behind"
        assert json.loads(config.config_path().read_text())["qdrant_url"]

    def test_a_json_array_is_rejected(self, data_dir):
        config.config_path().write_text("[1, 2, 3]")
        with pytest.raises(ValueError):
            config.load_config()


@pytest.mark.integration
@pytest.mark.slow
class TestRealMultiSession:
    """The whole point, against a real downloaded server and two real processes.

    Marked integration+slow: it downloads ~27 MB and starts a server, so an
    ordinary `pytest` run skips it. Run with:
        pytest tests/test_qdrant_server.py -m "integration and slow"
    """

    def test_two_processes_share_one_store_via_the_config_file(self, tmp_path):
        import subprocess
        import sys
        import textwrap

        env = {**__import__("os").environ, "MGCP_DATA_DIR": str(tmp_path)}
        setup = subprocess.run(
            [sys.executable, "-m", "mgcp.qdrant_server", "setup"],
            capture_output=True, text=True, env=env, timeout=600,
        )
        assert setup.returncode == 0, setup.stderr
        try:
            writer = textwrap.dedent("""
                import asyncio, sys, warnings
                warnings.filterwarnings("ignore")
                import mgcp.server as srv
                async def main():
                    await srv._ensure_initialized()
                    tag = sys.argv[1]
                    for i in range(2):
                        r = await srv.add_lesson(id=f"{tag}-{i}", trigger=f"{tag} trigger {i}", action="a")
                        assert "added successfully" in r, r
                    vs, _ = await srv._ensure_vector_stores()
                    print(len([x for x in vs.get_all_ids() if "-" in x]))
                asyncio.run(main())
            """)
            first = subprocess.run(
                [sys.executable, "-c", writer, "alpha"],
                capture_output=True, text=True, env=env, timeout=300,
            )
            assert first.returncode == 0, first.stderr
            second = subprocess.run(
                [sys.executable, "-c", writer, "beta"],
                capture_output=True, text=True, env=env, timeout=300,
            )
            assert second.returncode == 0, second.stderr
            # The second process must see both its own and the first's lessons.
            assert int(second.stdout.strip().splitlines()[-1]) >= 4
        finally:
            subprocess.run(
                [sys.executable, "-m", "mgcp.qdrant_server", "stop"],
                capture_output=True, env=env, timeout=120,
            )
