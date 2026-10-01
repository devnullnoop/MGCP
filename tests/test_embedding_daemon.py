"""Tests for the shared embedding daemon (v3 workstream B).

The load-bearing assertion in this file is that a vector computed in the daemon
and a vector computed in process are **exactly** equal, not merely close. The
daemon transports raw little-endian float32, which is the dtype
``SentenceTransformer.encode`` already produces, so there is no tolerance to
allow and any difference would mean the two paths had drifted.

The second load-bearing test starts two real OS processes. A single client never
contends, so every check-then-act window stays shut and a broken daemon looks
finished — which is exactly how the Qdrant 409 nearly shipped.

conftest sets ``MGCP_EMBED_DAEMON=0`` for the whole suite; these tests turn it
back on against a socket under tmp_path and are the only place it is enabled.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

import pytest

from mgcp import embedding
from mgcp.embedding_daemon import (
    EmbeddingDaemon,
    _socket_answers,
    autostart_enabled,
    daemon_enabled,
    get_socket_path,
    pack_vectors,
    recv_frame,
    send_frame,
    unpack_vectors,
)

TEXT = "git commit requires query_lessons first and the gate is enforcing"


@pytest.fixture
def short_tmp_dir():
    """A temp directory with a path short enough for a unix socket.

    pytest's ``tmp_path`` is nested deep enough to exceed the 104-byte
    ``sun_path`` limit on macOS, which is a property of the test harness and not
    of MGCP — ``~/.mgcp/embed.sock`` is 30 bytes. The limit itself is covered by
    ``test_overlong_socket_path_is_refused_not_crashed``.
    """
    path = Path(tempfile.mkdtemp(prefix="mgcp-s-", dir=tempfile.gettempdir()))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def daemon_socket(short_tmp_dir, monkeypatch):
    """A real daemon on a real unix socket, served from this process.

    Threaded rather than forked so the already-cached model is reused: the
    socket, the framing and the dispatch are all genuine, and process separation
    is covered separately by ``test_two_real_processes_share_one_daemon``.
    """
    socket_path = short_tmp_dir / "embed.sock"
    monkeypatch.setenv("MGCP_EMBED_DAEMON", "1")
    monkeypatch.setenv("MGCP_EMBED_AUTOSTART", "0")
    monkeypatch.setenv("MGCP_EMBED_SOCKET", str(socket_path))
    _reset_client_state()

    daemon = EmbeddingDaemon(socket_path, idle_timeout=0)
    assert daemon.bind(), "fresh tmp_path socket should bind"
    thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    thread.start()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _socket_answers(socket_path, timeout=0.5):
            break
        time.sleep(0.1)
    else:
        daemon.shutdown()
        pytest.fail("daemon did not become answerable")

    yield socket_path

    daemon.shutdown()
    _reset_client_state()
    thread.join(timeout=5)


def _reset_client_state() -> None:
    embedding._spawn_attempted = False
    embedding._drop_connection()


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------


def test_vector_packing_is_lossless_for_float32():
    """A float32 vector survives pack->unpack with no change at all."""
    import numpy as np

    original = np.random.default_rng(0).standard_normal(768).astype("<f4")
    restored = unpack_vectors(pack_vectors([original.tolist()]), 768)
    assert restored[0] == original.tolist()


def test_unpack_rejects_a_payload_that_is_not_whole_vectors():
    with pytest.raises(ValueError, match="not a multiple"):
        unpack_vectors(b"\x00" * 100, 768)


def test_frames_survive_a_short_read():
    """recv_exactly must reassemble a payload the kernel delivers in pieces."""
    a, b = socket.socketpair()
    payload = b"x" * 200_000
    threading.Thread(target=lambda: send_frame(a, b"V", payload), daemon=True).start()
    type_byte, body = recv_frame(b)
    assert type_byte == b"V"
    assert body == payload
    a.close()
    b.close()


def test_oversized_frame_is_refused():
    a, b = socket.socketpair()
    a.sendall(struct.pack("!I", 9 * 1024 * 1024))
    with pytest.raises(ValueError, match="out of range"):
        recv_frame(b)
    a.close()
    b.close()


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


def test_suite_runs_with_the_daemon_disabled():
    """conftest must keep an ordinary pytest run off the daemon entirely."""
    assert os.environ.get("MGCP_EMBED_DAEMON") == "0"
    assert daemon_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_daemon_can_be_disabled_by_several_spellings(value, monkeypatch):
    monkeypatch.setenv("MGCP_EMBED_DAEMON", value)
    assert daemon_enabled() is False
    monkeypatch.setenv("MGCP_EMBED_AUTOSTART", value)
    assert autostart_enabled() is False


def test_socket_path_follows_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("MGCP_EMBED_SOCKET", raising=False)
    monkeypatch.setenv("MGCP_DATA_DIR", str(tmp_path))
    assert get_socket_path() == tmp_path / "embed.sock"
    monkeypatch.setenv("MGCP_EMBED_SOCKET", str(tmp_path / "other.sock"))
    assert get_socket_path() == tmp_path / "other.sock"


# ---------------------------------------------------------------------------
# the correctness gate
# ---------------------------------------------------------------------------


def test_daemon_and_in_process_vectors_are_bit_identical(daemon_socket):
    """Not approximately equal. Identical. Any drift here is a corpus-wide bug."""
    assert embedding.embed(TEXT) == embedding.embed_in_process(TEXT)
    assert embedding.embed_query(TEXT) == embedding.embed_query_in_process(TEXT)

    texts = [TEXT, "lessons bridge through community summaries", "REM cadence is per project"]
    assert embedding.embed_batch(texts) == embedding.embed_batch_in_process(texts)


def test_daemon_really_was_used(daemon_socket):
    """Guard against the whole suite passing because everything fell back.

    Without this, every assertion above would still hold with the daemon dead —
    the fallback produces the same vectors, which is the point of the fallback
    and also what would hide its own failure.
    """
    status = embedding.daemon_status()
    assert status["mode"] == "daemon", status
    assert status["dimension"] == 768
    assert status["model"] == embedding.MODEL_NAME

    embedding.embed(TEXT)
    assert embedding._local.conn is not None


def test_query_prefix_changes_the_vector(daemon_socket):
    """embed and embed_query must not be the same call over the wire."""
    assert embedding.embed(TEXT) != embedding.embed_query(TEXT)


def test_empty_batch_needs_no_daemon(daemon_socket):
    assert embedding.embed_batch([]) == []


def test_large_batch_round_trips(daemon_socket):
    texts = [f"lesson number {i} about enforcement gates" for i in range(32)]
    vectors = embedding.embed_batch(texts)
    assert len(vectors) == 32
    assert all(len(v) == 768 for v in vectors)
    assert vectors == embedding.embed_batch_in_process(texts)


# ---------------------------------------------------------------------------
# degradation — a dead daemon must cost speed, never correctness
# ---------------------------------------------------------------------------


def test_no_daemon_falls_back_silently(short_tmp_dir, monkeypatch):
    monkeypatch.setenv("MGCP_EMBED_DAEMON", "1")
    monkeypatch.setenv("MGCP_EMBED_AUTOSTART", "0")
    monkeypatch.setenv("MGCP_EMBED_SOCKET", str(short_tmp_dir / "absent.sock"))
    _reset_client_state()

    assert embedding.daemon_status()["mode"] == "in-process"
    assert embedding.embed(TEXT) == embedding.embed_in_process(TEXT)


def test_daemon_dying_mid_session_falls_back(daemon_socket, monkeypatch):
    """The connection is persistent, so a daemon that idles out or is killed
    presents as a broken pipe on the next call rather than a failure to connect.
    """
    first = embedding.embed(TEXT)
    assert embedding._local.conn is not None

    # Kill the socket under the client without clearing its cached connection.
    Path(daemon_socket).unlink()
    conn = embedding._local.conn
    conn.close()

    assert embedding.embed(TEXT) == first


def test_unknown_op_returns_an_error_frame_and_the_client_falls_back(daemon_socket):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(daemon_socket))
    send_frame(conn, b"J", json.dumps({"op": "nonsense"}).encode())
    type_byte, body = recv_frame(conn)
    conn.close()
    assert type_byte == b"E"
    assert "nonsense" in body.decode()

    # An error on one request must not have poisoned the daemon.
    assert embedding.embed(TEXT) == embedding.embed_in_process(TEXT)


def test_a_malformed_request_does_not_kill_the_daemon(daemon_socket):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(daemon_socket))
    body = b"J" + b"{not json"
    conn.sendall(struct.pack("!I", len(body)) + body)
    type_byte, _ = recv_frame(conn)
    conn.close()
    assert type_byte == b"E"
    assert _socket_answers(daemon_socket)


# ---------------------------------------------------------------------------
# contention
# ---------------------------------------------------------------------------


def test_bind_declines_when_a_live_daemon_owns_the_socket(daemon_socket):
    """The loser of a start race reports 'already owned', not an error."""
    second = EmbeddingDaemon(Path(daemon_socket), idle_timeout=0)
    assert second.bind() is False


def test_stale_socket_file_is_reclaimed(short_tmp_dir):
    """A killed daemon leaves a socket file that looks identical to a live one."""
    socket_path = short_tmp_dir / "stale.sock"
    socket_path.write_bytes(b"")  # a file where a socket should be
    daemon = EmbeddingDaemon(socket_path, idle_timeout=0)
    try:
        assert daemon.bind() is True
    finally:
        daemon.shutdown()


def test_concurrent_clients_are_all_served(daemon_socket):
    """Sixteen threads, one daemon, every answer correct."""
    expected = embedding.embed_in_process(TEXT)
    results: list[list[float]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work():
        try:
            # Each thread gets its own connection; interleaved frames on a
            # shared socket would mismatch request and response.
            value = embedding.embed(TEXT)
            with lock:
                results.append(value)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not errors, errors
    assert len(results) == 16
    assert all(r == expected for r in results)


@pytest.mark.slow
def test_two_real_processes_share_one_daemon(short_tmp_dir):
    """Process separation, proven with two real OS processes and one daemon.

    This is the test the suite could not otherwise give: a threaded daemon
    shares the parent's already-loaded model, so it proves the protocol but not
    the saving. Here each client is a separate interpreter, and the assertion is
    that neither one ever loads the model — ``get_embedding_model`` is left
    untouched in both, while the vectors still come back correct.
    """
    socket_path = short_tmp_dir / "embed.sock"
    env = {
        **os.environ,
        "MGCP_EMBED_DAEMON": "1",
        "MGCP_EMBED_AUTOSTART": "1",
        "MGCP_EMBED_SOCKET": str(socket_path),
    }
    client = textwrap.dedent(
        """
        import json, sys
        from mgcp import embedding
        vector = embedding.embed(sys.argv[1])
        print(json.dumps({
            "mode": embedding.daemon_status()["mode"],
            "loaded_here": embedding.get_embedding_model.cache_info().currsize > 0,
            "dims": len(vector),
            "head": vector[:4],
        }))
        """
    )
    script = short_tmp_dir / "client.py"
    script.write_text(client)

    outputs = []
    try:
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, str(script), TEXT],
                capture_output=True, text=True, env=env, timeout=300,
            )
            assert proc.returncode == 0, proc.stderr[-2000:]
            outputs.append(json.loads(proc.stdout.strip().splitlines()[-1]))

        for out in outputs:
            assert out["mode"] == "daemon", out
            assert out["loaded_here"] is False, "client loaded its own model copy"
            assert out["dims"] == 768

        assert outputs[0]["head"] == outputs[1]["head"], "two processes disagreed"
    finally:
        if _socket_answers(socket_path):
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.settimeout(5)
            conn.connect(str(socket_path))
            send_frame(conn, b"J", json.dumps({"op": "shutdown"}).encode())
            conn.close()


def test_overlong_socket_path_is_refused_not_crashed(tmp_path, monkeypatch):
    """A path over the AF_UNIX limit must be reported, not raised as a bare OSError.

    Found by this suite: pytest's own tmp_path exceeds the 104-byte sun_path
    limit, so bind() died with "AF_UNIX path too long" and the client then waited
    the full 30-second startup timeout before falling back. A deep MGCP_DATA_DIR
    does the same thing in production.
    """
    from mgcp.embedding_daemon import socket_path_is_usable

    long_path = tmp_path / ("d" * 120) / "embed.sock"
    assert socket_path_is_usable(long_path) is False
    assert socket_path_is_usable(Path.home() / ".mgcp" / "embed.sock") is True

    with pytest.raises(ValueError, match="unix socket limit"):
        EmbeddingDaemon(long_path).bind()

    monkeypatch.setenv("MGCP_EMBED_DAEMON", "1")
    monkeypatch.setenv("MGCP_EMBED_AUTOSTART", "1")
    monkeypatch.setenv("MGCP_EMBED_SOCKET", str(long_path))
    _reset_client_state()

    status = embedding.daemon_status()
    assert status["mode"] == "in-process"
    assert "AF_UNIX" in status["reason"]

    # And it must not have spent the startup timeout finding that out.
    started = time.monotonic()
    assert embedding.embed(TEXT) == embedding.embed_in_process(TEXT)
    assert time.monotonic() - started < 10, "client waited on a daemon that could never bind"
