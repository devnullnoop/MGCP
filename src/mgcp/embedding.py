"""Centralized embedding model for MGCP.

Uses BAAI/bge-base-en-v1.5 for improved retrieval quality over all-MiniLM-L6-v2.
- Dimensions: 768 (vs 384)
- MTEB benchmark: ~7% better NDCG
- Size: ~415MB on disk; ~478 MiB resident once loaded

``embed`` / ``embed_query`` / ``embed_batch`` are the only functions callers
should use, and their signatures have never changed. What changed in v3 is where
the work happens: if a shared embedding daemon is answering, these send the text
over a unix socket to a process that already holds the model; otherwise they load
it here, exactly as they always did. A caller cannot tell the difference, and the
vectors are bit-identical either way because the daemon transports raw float32 —
the dtype ``encode`` already produces — and computes them by calling the
``*_in_process`` functions below.

Why that matters: the model is ~478 MiB resident, and every MCP session, the
dashboard on its first write, and every CLI used to load its own copy. Three
concurrent sessions cost about 1.4 GiB of identical weights. The socket round
trip was measured at 0.009 ms on a persistent connection and 0.060 ms connecting
per call, against 8.2 ms for the model call itself.

Control:
- ``MGCP_EMBED_DAEMON=0``   never use or start a daemon (what the test suite sets)
- ``MGCP_EMBED_AUTOSTART=0`` use a daemon that is running, but never start one
- ``MGCP_EMBED_SOCKET``     socket path override

API Reference:
- sentence-transformers: https://www.sbert.net/
- BGE models: https://huggingface.co/BAAI/bge-base-en-v1.5
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger("mgcp.embedding")

# Model configuration
MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBEDDING_DIMENSION = 768

# BGE instruction prefix for query embeddings (improves retrieval quality)
# See: https://huggingface.co/BAAI/bge-base-en-v1.5#usage
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Get the shared embedding model instance.

    Uses lru_cache to ensure single instance across all stores. First call
    downloads the model (~415MB) if not cached. In a process that reaches the
    daemon this is never called at all, which is the entire point.
    """
    from sentence_transformers import SentenceTransformer

    logger.info(f"Loading embedding model: {MODEL_NAME}")
    try:
        model = SentenceTransformer(MODEL_NAME, local_files_only=True)
    except OSError:
        # First run: download the model
        model = SentenceTransformer(MODEL_NAME)
    logger.info(f"Embedding model loaded (dimension={EMBEDDING_DIMENSION})")
    return model


# ---------------------------------------------------------------------------
# in-process path — the daemon calls these, so both paths share one definition
# ---------------------------------------------------------------------------


def embed_in_process(text: str) -> list[float]:
    """Embed one string using a model loaded in this process."""
    model = get_embedding_model()
    # encode() returns numpy array, convert to list for Qdrant
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def embed_query_in_process(text: str) -> list[float]:
    """Embed one query, with the BGE instruction prefix, in this process."""
    model = get_embedding_model()
    embedding = model.encode(QUERY_INSTRUCTION + text, normalize_embeddings=True)
    return embedding.tolist()


def embed_batch_in_process(texts: list[str]) -> list[list[float]]:
    """Embed many strings in this process."""
    if not texts:
        return []
    model = get_embedding_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]


# ---------------------------------------------------------------------------
# daemon client
# ---------------------------------------------------------------------------

_local = threading.local()
_spawn_lock = threading.Lock()
_spawn_attempted = False


def _connection() -> socket.socket | None:
    """A per-thread persistent connection to the daemon, or None.

    Persistent because reconnecting costs 0.060 ms against 0.009 ms reused —
    both negligible, but there is no reason to pay the larger one on every
    query. Per-thread because a socket cannot be shared across threads that
    interleave request and response frames.
    """
    from .embedding_daemon import (
        autostart_enabled,
        daemon_enabled,
        get_socket_path,
        socket_path_is_usable,
    )

    if not daemon_enabled():
        return None

    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    socket_path = get_socket_path()
    if not socket_path_is_usable(socket_path):
        # No daemon can ever bind this path, so do not spend the startup
        # timeout discovering that. Fall back immediately.
        logger.info("socket path too long for AF_UNIX (%s); embedding in process", socket_path)
        _local.conn = None
        return None
    conn = _try_connect(socket_path)
    if conn is None and autostart_enabled() and _spawn_daemon(socket_path):
        conn = _try_connect(socket_path)
    _local.conn = conn
    return conn


def _try_connect(socket_path) -> socket.socket | None:
    if not os.path.exists(socket_path):
        return None
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(120.0)
        conn.connect(str(socket_path))
        return conn
    except OSError:
        return None


def _spawn_daemon(socket_path) -> bool:
    """Start a daemon and wait for it to answer. Once per process.

    Returns True if a daemon is answering when this returns — including one
    somebody else started, which is a success and not a race lost.
    """
    global _spawn_attempted
    from .embedding_daemon import STARTUP_TIMEOUT, _socket_answers

    with _spawn_lock:
        if _spawn_attempted:
            return _socket_answers(socket_path)
        _spawn_attempted = True

        try:
            subprocess.Popen(
                [sys.executable, "-m", "mgcp.embedding_daemon", "--socket", str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                # Outlive the process that started it: the saving only exists if
                # the daemon survives the session that happened to need it first.
                start_new_session=True,
            )
        except OSError as exc:
            logger.info("could not start embedding daemon (%s); embedding in process", exc)
            return False

        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if _socket_answers(socket_path, timeout=0.5):
                logger.info("embedding daemon ready at %s", socket_path)
                return True
            time.sleep(0.25)
        logger.info("embedding daemon did not become ready; embedding in process")
        return False


def _drop_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        with contextlib.suppress(OSError):
            conn.close()
    _local.conn = None


def _request(payload: dict) -> list[list[float]] | None:
    """Ask the daemon. None means "no daemon" or "it failed" — caller falls back.

    Any failure here is recoverable by definition: the in-process path produces
    the same vectors. So a dead daemon degrades performance and never
    correctness, and is never raised to the caller.
    """
    from .embedding_daemon import recv_frame, send_frame, unpack_vectors

    for attempt in (1, 2):
        conn = _connection()
        if conn is None:
            return None
        try:
            send_frame(conn, b"J", json.dumps(payload).encode())
            frame = recv_frame(conn)
            if frame is None:
                raise OSError("daemon closed the connection")
            type_byte, body = frame
            if type_byte == b"E":
                logger.warning("embedding daemon error: %s", body.decode(errors="replace"))
                return None
            if type_byte != b"V":
                raise ValueError(f"unexpected reply type {type_byte!r}")
            return unpack_vectors(body, EMBEDDING_DIMENSION)
        except (OSError, ValueError) as exc:
            # The daemon may have idled out between our last call and this one,
            # which looks exactly like a broken pipe. Reconnect once.
            _drop_connection()
            if attempt == 2:
                logger.info("embedding daemon unreachable (%s); embedding in process", exc)
                return None
    return None


def daemon_status() -> dict:
    """Describe the embedding path, for the dashboard health endpoint and the CLI."""
    from .embedding_daemon import (
        autostart_enabled,
        daemon_enabled,
        get_socket_path,
        socket_path_is_usable,
    )

    socket_path = get_socket_path()
    if not daemon_enabled():
        return {"mode": "in-process", "reason": "disabled by MGCP_EMBED_DAEMON", "socket": str(socket_path)}
    if not socket_path_is_usable(socket_path):
        return {"mode": "in-process", "reason": "socket path exceeds the AF_UNIX limit", "socket": str(socket_path)}

    info: dict = {"socket": str(socket_path), "autostart": autostart_enabled()}
    conn = _try_connect(socket_path)
    if conn is None:
        info.update({"mode": "in-process", "reason": "no daemon answering"})
        return info
    try:
        from .embedding_daemon import recv_frame, send_frame

        send_frame(conn, b"J", json.dumps({"op": "ping"}).encode())
        frame = recv_frame(conn)
        if frame and frame[0] == b"J":
            info.update({"mode": "daemon", **json.loads(frame[1])})
        else:
            info.update({"mode": "in-process", "reason": "daemon did not answer a ping"})
    except (OSError, ValueError) as exc:
        info.update({"mode": "in-process", "reason": f"ping failed: {exc}"})
    finally:
        with contextlib.suppress(OSError):
            conn.close()
    return info


# ---------------------------------------------------------------------------
# public API — unchanged signatures, daemon-first, in-process fallback
# ---------------------------------------------------------------------------


def embed(text: str) -> list[float]:
    """Embed a single text string.

    Args:
        text: Text to embed

    Returns:
        List of floats representing the embedding vector (768 dimensions)
    """
    vectors = _request({"op": "embed", "text": text})
    if vectors:
        return vectors[0]
    return embed_in_process(text)


def embed_query(text: str) -> list[float]:
    """Embed a query with BGE instruction prefix for better retrieval.

    BGE models produce better search results when queries are prefixed with
    an instruction string. This must only be used for queries, not for
    documents/passages being stored.

    Args:
        text: Query text to embed

    Returns:
        List of floats representing the embedding vector (768 dimensions)
    """
    vectors = _request({"op": "embed_query", "text": text})
    if vectors:
        return vectors[0]
    return embed_query_in_process(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts efficiently.

    Args:
        texts: List of texts to embed

    Returns:
        List of embedding vectors
    """
    if not texts:
        return []

    vectors = _request({"op": "embed_batch", "texts": texts})
    if vectors is not None and len(vectors) == len(texts):
        return vectors
    return embed_batch_in_process(texts)
