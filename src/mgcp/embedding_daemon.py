"""Shared embedding daemon — BGE loaded once per machine, not once per process.

Every MGCP process that embeds anything (each MCP session, the dashboard on its
first write, every CLI) otherwise loads its own copy of
``BAAI/bge-base-en-v1.5``. Measured peak RSS for a process that does nothing but
embed one sentence is **478 MiB**. Three concurrent sessions therefore cost about
1.4 GiB of identical weights, which is the thing that made multi-session access
expensive rather than impossible.

This daemon loads the model once and answers over a unix domain socket in the
MGCP data directory. Unix socket rather than TCP deliberately: it is not
reachable off-box, so it needs no authentication, and the measured round trip is
**0.009 ms** on a persistent connection and **0.060 ms** connecting per call,
against **8.2 ms** for the model call itself. Transport is 0.7% of the work in
the worst case — the design estimated 1-3 ms and was pessimistic by two orders of
magnitude.

Vectors are transported as raw little-endian float32, which is exactly the dtype
``SentenceTransformer.encode`` produces, so a value that crosses the socket is
bit-identical to one computed in process. ``tests/test_embedding_daemon.py``
asserts that equality; the two paths cannot silently drift.

Wire protocol, both directions: a 4-byte big-endian length followed by that many
bytes. A request body is JSON. A response body is one type byte plus a payload:

    V  <n * 768 * 4 bytes>   one or more float32 vectors
    J  <utf-8 json>          metadata (ping)
    E  <utf-8 text>          error; the client falls back in-process

Run it explicitly with ``mgcp-embed``, or let the first client start it.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("mgcp.embedding_daemon")

# Default idle shutdown. A one-off CLI should not leave 478 MiB resident for the
# rest of the day, but a working session should not pay the 3-second cold load
# repeatedly either.
DEFAULT_IDLE_TIMEOUT = 900.0

# How long a client waits for a daemon it just spawned to become answerable.
# The cold model load measured 3.0s; the ceiling is generous because losing this
# race is not an error, it only means falling back to in-process embedding.
STARTUP_TIMEOUT = 30.0

MAX_REQUEST_BYTES = 8 * 1024 * 1024

# A unix socket path lives in sockaddr_un.sun_path, which is 104 bytes on macOS
# and 108 on Linux. Exceeding it raises a bare "AF_UNIX path too long" OSError
# from bind(), which told the daemon nothing useful and left a client waiting the
# full startup timeout before falling back. A deep MGCP_DATA_DIR is enough to
# trigger it, so it is checked up front and reported as what it is.
MAX_SOCKET_PATH_BYTES = 100


def socket_path_is_usable(socket_path: Path) -> bool:
    return len(str(socket_path).encode()) <= MAX_SOCKET_PATH_BYTES


def get_socket_path() -> Path:
    """Socket location, honouring the same data-dir override as every store."""
    explicit = os.environ.get("MGCP_EMBED_SOCKET")
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("MGCP_DATA_DIR") or str(Path.home() / ".mgcp")
    return Path(data_dir) / "embed.sock"


def daemon_enabled() -> bool:
    """False disables both using and starting the daemon.

    The suite sets ``MGCP_EMBED_DAEMON=0`` so that an ordinary test run neither
    spawns a background process nor depends on one the operator happens to have
    running. Tests that exercise the daemon turn it back on against a socket in
    a temp directory.
    """
    return os.environ.get("MGCP_EMBED_DAEMON", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def autostart_enabled() -> bool:
    """False uses a daemon that is already running but never starts one."""
    return os.environ.get("MGCP_EMBED_AUTOSTART", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------


def recv_exactly(sock: socket.socket, count: int) -> bytes | None:
    """Read exactly ``count`` bytes, or None if the peer closed first.

    ``recv`` is not obliged to return everything asked for; a short read on the
    96 KB response of a 32-item batch is normal rather than exceptional.
    """
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, type_byte: bytes, payload: bytes) -> None:
    body = type_byte + payload
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_frame(sock: socket.socket) -> tuple[bytes, bytes] | None:
    header = recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack("!I", header)
    if length == 0 or length > MAX_REQUEST_BYTES:
        raise ValueError(f"frame length {length} out of range")
    body = recv_exactly(sock, length)
    if body is None:
        return None
    return body[:1], body[1:]


def pack_vectors(vectors: list[list[float]]) -> bytes:
    import numpy as np

    return np.asarray(vectors, dtype="<f4").tobytes()


def unpack_vectors(payload: bytes, dimension: int) -> list[list[float]]:
    import numpy as np

    flat = np.frombuffer(payload, dtype="<f4")
    if flat.size % dimension:
        raise ValueError(f"payload of {flat.size} floats is not a multiple of {dimension}")
    return [row.tolist() for row in flat.reshape(-1, dimension)]


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


class EmbeddingDaemon:
    """Serves embed / embed_query / embed_batch from one resident model."""

    def __init__(self, socket_path: Path, idle_timeout: float = DEFAULT_IDLE_TIMEOUT):
        self.socket_path = socket_path
        self.idle_timeout = idle_timeout
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self._last_activity = time.monotonic()
        self._active = 0
        self._lock = threading.Lock()

    def bind(self) -> bool:
        """Claim the socket. False means another daemon already owns it.

        Attempt-and-tolerate rather than check-then-act: two processes starting
        together both see no socket, and the loser of that race has nothing to
        report — the daemon it wanted now exists. This is the same mistake that
        shipped as a 409 in Qdrant server mode.
        """
        if not socket_path_is_usable(self.socket_path):
            raise ValueError(
                f"socket path is {len(str(self.socket_path).encode())} bytes, over the "
                f"{MAX_SOCKET_PATH_BYTES}-byte unix socket limit: {self.socket_path}. "
                "Set MGCP_EMBED_SOCKET to something shorter."
            )
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(self.socket_path))
        except OSError as exc:
            if exc.errno not in (errno.EADDRINUSE,):
                server.close()
                raise
            # A socket file is there. Either a live daemon owns it, or it is the
            # leftover of one that was killed — those look identical on disk and
            # differ only in whether anything answers.
            if _socket_answers(self.socket_path):
                server.close()
                return False
            logger.info("removing stale socket %s", self.socket_path)
            with contextlib.suppress(OSError):
                self.socket_path.unlink()
            try:
                server.bind(str(self.socket_path))
            except OSError:
                server.close()
                return False
        server.listen(64)
        server.settimeout(0.5)
        os.chmod(self.socket_path, 0o600)
        self._server = server
        return True

    def serve_forever(self) -> None:
        if self._server is None and not self.bind():
            logger.info("another daemon already owns %s; exiting", self.socket_path)
            return

        # Load before announcing readiness is pointless — the socket is already
        # bound and accepting, so a client could connect mid-load. Loading here,
        # before the accept loop, means the first client blocks on accept rather
        # than on an answer, which is the same wait either way.
        from .embedding import EMBEDDING_DIMENSION, MODEL_NAME, get_embedding_model

        self.dimension = EMBEDDING_DIMENSION
        self.model_name = MODEL_NAME
        started = time.monotonic()
        get_embedding_model()
        logger.info("model %s ready in %.1fs", MODEL_NAME, time.monotonic() - started)

        threading.Thread(target=self._reap_when_idle, daemon=True).start()
        assert self._server is not None
        try:
            while not self._stop.is_set():
                try:
                    conn, _ = self._server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()
        finally:
            self.shutdown()

    def _reap_when_idle(self) -> None:
        if self.idle_timeout <= 0:
            return
        while not self._stop.wait(1.0):
            with self._lock:
                idle_for = time.monotonic() - self._last_activity
                busy = self._active
            if not busy and idle_for >= self.idle_timeout:
                logger.info("idle for %.0fs; shutting down", idle_for)
                self._stop.set()
                # Nudge the accept loop out of its timeout.
                with contextlib.suppress(OSError):
                    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    probe.settimeout(0.2)
                    probe.connect(str(self.socket_path))
                    probe.close()
                return

    def _serve_client(self, conn: socket.socket) -> None:
        with self._lock:
            self._active += 1
            self._last_activity = time.monotonic()
        try:
            conn.settimeout(120.0)
            while not self._stop.is_set():
                try:
                    frame = recv_frame(conn)
                except (OSError, ValueError):
                    return
                if frame is None:
                    return
                _, body = frame
                try:
                    reply_type, payload = self._dispatch(body)
                except Exception as exc:  # a bad request must not kill the daemon
                    logger.warning("request failed: %s", exc)
                    reply_type, payload = b"E", str(exc).encode()
                with contextlib.suppress(OSError):
                    send_frame(conn, reply_type, payload)
                with self._lock:
                    self._last_activity = time.monotonic()
        finally:
            with self._lock:
                self._active -= 1
                self._last_activity = time.monotonic()
            with contextlib.suppress(OSError):
                conn.close()

    def _dispatch(self, body: bytes) -> tuple[bytes, bytes]:
        request = json.loads(body)
        op = request.get("op")

        if op == "ping":
            return b"J", json.dumps(
                {"model": self.model_name, "dimension": self.dimension, "pid": os.getpid()}
            ).encode()

        if op == "shutdown":
            self._stop.set()
            return b"J", json.dumps({"stopping": True}).encode()

        # Imported here so the daemon's own vectors come from exactly the
        # functions an in-process caller would have used.
        from . import embedding

        if op == "embed":
            return b"V", pack_vectors([embedding.embed_in_process(request["text"])])
        if op == "embed_query":
            return b"V", pack_vectors([embedding.embed_query_in_process(request["text"])])
        if op == "embed_batch":
            texts = request["texts"]
            if not texts:
                return b"V", b""
            return b"V", pack_vectors(embedding.embed_batch_in_process(texts))

        raise ValueError(f"unknown op {op!r}")

    def shutdown(self) -> None:
        self._stop.set()
        if self._server is not None:
            with contextlib.suppress(OSError):
                self._server.close()
            self._server = None
        with contextlib.suppress(OSError):
            self.socket_path.unlink()


def _socket_answers(socket_path: Path, timeout: float = 1.0) -> bool:
    """True if something on the other end of this socket responds to a ping."""
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(str(socket_path))
    except OSError:
        return False
    try:
        send_frame(conn, b"J", json.dumps({"op": "ping"}).encode())
        return recv_frame(conn) is not None
    except (OSError, ValueError):
        return False
    finally:
        with contextlib.suppress(OSError):
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mgcp-embed",
        description="Run the shared MGCP embedding daemon (BGE loaded once per machine).",
    )
    parser.add_argument("--socket", default=None, help="socket path (default: $MGCP_DATA_DIR/embed.sock)")
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT,
        help=f"seconds of inactivity before exiting; 0 disables (default: {DEFAULT_IDLE_TIMEOUT:.0f})",
    )
    parser.add_argument("--status", action="store_true", help="report whether a daemon is answering, and exit")
    parser.add_argument("--stop", action="store_true", help="ask a running daemon to exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    socket_path = Path(args.socket) if args.socket else get_socket_path()

    if args.status:
        from .embedding import daemon_status

        print(json.dumps(daemon_status(), indent=2))
        return 0

    if args.stop:
        if not _socket_answers(socket_path):
            print(f"no daemon answering at {socket_path}")
            return 1
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(5.0)
        conn.connect(str(socket_path))
        send_frame(conn, b"J", json.dumps({"op": "shutdown"}).encode())
        with contextlib.suppress(OSError, ValueError):
            recv_frame(conn)
        conn.close()
        print(f"shutdown requested at {socket_path}")
        return 0

    daemon = EmbeddingDaemon(socket_path, idle_timeout=args.idle_timeout)
    if not daemon.bind():
        print(f"a daemon is already answering at {socket_path}")
        return 0
    print(f"mgcp-embed listening on {socket_path}")
    try:
        daemon.serve_forever()
    except KeyboardInterrupt:
        daemon.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
