"""A local Qdrant server, installed and supervised by MGCP itself.

Embedded Qdrant permits one client per directory, so more than one MGCP session
needs a Qdrant *server*. Until now the only route to one was a Docker container
the user installed themselves, which `mgcp-init` never mentioned. A feature you
can only reach through an undocumented manual step is not shipped.

Qdrant publishes native binaries for macOS (arm64 and x86_64), Windows (x86_64)
and Linux, so this module downloads the one matching the machine, verifies it
against a checksum recorded here, puts it in the MGCP data directory, and runs
it on the loopback interface. No container, no package manager, no Rust
toolchain, nothing for the operator to fetch by hand.

Single-session MGCP still needs none of this: embedded remains the default and
this server is started only once `qdrant_url` is in the config.
"""

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import signal
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .config import get_data_dir, get_value, set_value

logger = logging.getLogger("mgcp.qdrant_server")

# Must stay within one minor of qdrant-client in pyproject.toml: the client
# refuses a spread wider than one minor and warns on every connection.
# tests/test_qdrant_server.py pins the two together so a bump cannot be
# half-applied.
QDRANT_VERSION = "1.19.1"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6333
RELEASE_URL = "https://github.com/qdrant/qdrant/releases/download/v{version}/{asset}"

# sha256 of each official release archive, recorded by downloading all of them
# at the pinned version. Qdrant publishes no checksum file of its own, so these
# are the integrity check: without them "verified" would mean nothing more than
# "the download finished".
ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("darwin", "arm64"): (
        "qdrant-aarch64-apple-darwin.tar.gz",
        "e060209dfefc9d977ddcec48521349f505f8fd1ce21f2a3db444140870522fe4",
    ),
    ("darwin", "x86_64"): (
        "qdrant-x86_64-apple-darwin.tar.gz",
        "ba7cbada9a90aefdbd7f92de4e093cd25328206bd65e9e96657bd6133d272637",
    ),
    ("windows", "x86_64"): (
        "qdrant-x86_64-pc-windows-msvc.zip",
        "9b6f69bd85f6abed4bc13f943099f55c6ffd55f5dd90388635320d8fbb569eb0",
    ),
    ("linux", "x86_64"): (
        "qdrant-x86_64-unknown-linux-gnu.tar.gz",
        "eef986e769d4d3e806dd2d546e1b4ecdd416211e54d34b4ed764fac7c58e1085",
    ),
    ("linux", "aarch64"): (
        "qdrant-aarch64-unknown-linux-musl.tar.gz",
        "0e607c11705fab22f7d667f4749bc0b6b60a8fa9e91de71880a6ebafbbda1b26",
    ),
}

DOWNLOAD_TIMEOUT = 300.0
START_TIMEOUT = 60.0
STOP_TIMEOUT = 30.0


class QdrantServerError(RuntimeError):
    """Install or supervise failed, with a message the operator can act on."""


def platform_key() -> tuple[str, str]:
    """(system, arch) normalised to the keys in ASSETS.

    `platform.machine()` reports arm64 on macOS and aarch64 on Linux for the
    same instruction set, and AMD64 on Windows where everyone else says x86_64.
    Normalising here keeps one spelling per row.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        machine = "x86_64"
    elif machine in ("aarch64", "arm64"):
        # macOS keeps arm64; Linux publishes the same CPU as aarch64.
        machine = "arm64" if system == "darwin" else "aarch64"
    return system, machine


def asset_for_platform() -> tuple[str, str]:
    """The archive name and sha256 for this machine."""
    key = platform_key()
    if key not in ASSETS:
        supported = ", ".join(f"{s}/{m}" for s, m in sorted(ASSETS))
        raise QdrantServerError(
            f"No Qdrant binary is published for {key[0]}/{key[1]}. Supported here: "
            f"{supported}. Multi-session needs a Qdrant server reachable over HTTP; "
            "run one however you like and put its URL in `qdrant_url` "
            f"({get_data_dir() / 'config.json'})."
        )
    return ASSETS[key]


def bin_dir() -> Path:
    return get_data_dir() / "bin"


def binary_path() -> Path:
    """Where the server binary lives once installed."""
    name = "qdrant.exe" if platform_key()[0] == "windows" else "qdrant"
    return bin_dir() / name


def storage_dir() -> Path:
    """Where the server keeps its data. This is not the embedded directory.

    Sharing one directory between the embedded store and the server would mean
    two different processes owning the same files with different assumptions.
    SQLite stays the source of truth and `mgcp-migrate` rebuilds either index.
    """
    return get_data_dir() / "qdrant-server"


def pid_file() -> Path:
    return get_data_dir() / "qdrant-server.pid"


def log_file() -> Path:
    return get_data_dir() / "logs" / "qdrant-server.log"


def default_url(port: int = DEFAULT_PORT) -> str:
    return f"http://{DEFAULT_HOST}:{port}"


def installed_version() -> str | None:
    """The version of the installed binary, or None when it is absent.

    Runs the binary rather than trusting the filename: a half-extracted or
    wrong-architecture download is otherwise indistinguishable from a good one
    until the first start fails.
    """
    path = binary_path()
    if not path.exists():
        return None
    try:
        out = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    # "qdrant 1.19.1"
    parts = out.split()
    return parts[-1] if parts else None


def _tls_context() -> ssl.SSLContext:
    """A context that can actually verify github.com.

    The python.org macOS framework build ships no CA bundle, so `urlopen` fails
    with CERTIFICATE_VERIFY_FAILED until somebody runs Install Certificates.command.
    certifi is already in the tree (httpx, via qdrant-client, requires it) and is
    now declared directly, so the installer verifies TLS on every platform rather
    than depending on how the interpreter was installed. There is no option to
    switch the check off. The checksum protects the bytes, and a download that
    cannot verify the server it is talking to should not be easy to arrange.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi is a declared dependency
        return ssl.create_default_context()


def _download(url: str, dest: Path, progress: bool = True) -> None:
    try:
        with urllib.request.urlopen(
            url, timeout=DOWNLOAD_TIMEOUT, context=_tls_context()
        ) as response:
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            last_pct = -10
            with dest.open("wb") as handle:
                while chunk := response.read(256 * 1024):
                    handle.write(chunk)
                    written += len(chunk)
                    if progress and total:
                        pct = written * 100 // total
                        if pct >= last_pct + 10:
                            print(f"  {pct}% ({written / 1048576:.0f} MB)", flush=True)
                            last_pct = pct
    except urllib.error.URLError as exc:
        raise QdrantServerError(
            f"Could not download {url}: {exc}. The archive is ~30 MB from "
            "github.com; a proxy or offline machine will need it fetched by hand "
            f"and extracted to {binary_path()}."
        ) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_binary(archive: Path, into: Path) -> Path:
    """Pull the single `qdrant` binary out of the archive.

    Members are checked rather than trusted: this is an archive from the
    network, and `extractall` on an archive containing `../` writes outside the
    destination.
    """
    into.mkdir(parents=True, exist_ok=True)
    wanted = "qdrant.exe" if archive.suffix == ".zip" else "qdrant"

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if wanted not in names:
                raise QdrantServerError(f"{archive.name} does not contain {wanted}: {names}")
            with zf.open(wanted) as src, (into / wanted).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            member = next((m for m in tf.getmembers() if m.name == wanted), None)
            if member is None:
                raise QdrantServerError(
                    f"{archive.name} does not contain {wanted}: {tf.getnames()}"
                )
            extracted = tf.extractfile(member)
            if extracted is None:
                raise QdrantServerError(f"{wanted} in {archive.name} is not a regular file")
            with extracted, (into / wanted).open("wb") as dst:
                shutil.copyfileobj(extracted, dst)

    target = into / wanted
    target.chmod(0o755)
    return target


def install(force: bool = False, progress: bool = True) -> Path:
    """Download, verify and install the Qdrant binary for this machine."""
    existing = installed_version()
    if existing == QDRANT_VERSION and not force:
        if progress:
            print(f"Qdrant {existing} already installed at {binary_path()}")
        return binary_path()

    asset, expected_sha = asset_for_platform()
    url = RELEASE_URL.format(version=QDRANT_VERSION, asset=asset)
    if progress:
        print(f"Downloading {asset} ({QDRANT_VERSION})...")

    with tempfile.TemporaryDirectory(prefix="mgcp-qdrant-") as tmpdir:
        archive = Path(tmpdir) / asset
        _download(url, archive, progress=progress)

        actual_sha = _sha256(archive)
        if actual_sha != expected_sha:
            raise QdrantServerError(
                f"Checksum mismatch for {asset}.\n  expected {expected_sha}\n"
                f"  actual   {actual_sha}\nRefusing to install. Qdrant publishes no "
                "checksum file, so this value was recorded from the release at the "
                "pinned version; a mismatch means the archive changed or the "
                "download is corrupt."
            )
        if progress:
            print(f"  checksum OK ({actual_sha[:16]}...)")
        _extract_binary(archive, bin_dir())

    version = installed_version()
    if version != QDRANT_VERSION:
        raise QdrantServerError(
            f"Installed binary reports version {version!r}, expected {QDRANT_VERSION}. "
            f"Remove {binary_path()} and retry."
        )
    if progress:
        print(f"Installed Qdrant {version} -> {binary_path()}")
    return binary_path()


def is_answering(url: str, timeout: float = 2.0) -> bool:
    """Is a Qdrant answering on this URL?"""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/healthz", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def running_pid() -> int | None:
    """The pid of a server we started that is still alive."""
    path = pid_file()
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def start(port: int = DEFAULT_PORT, wait: bool = True, progress: bool = True) -> str:
    """Start the local Qdrant, or return the URL if one already answers."""
    url = default_url(port)
    if is_answering(url):
        if progress:
            print(f"Qdrant already answering on {url}")
        return url

    if installed_version() is None:
        raise QdrantServerError(
            f"No Qdrant binary at {binary_path()}. Run `mgcp-qdrant install` first, "
            "or `mgcp-qdrant setup` to install, start and configure in one step."
        )

    storage_dir().mkdir(parents=True, exist_ok=True)
    log_file().parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({
        "QDRANT__SERVICE__HOST": DEFAULT_HOST,
        "QDRANT__SERVICE__HTTP_PORT": str(port),
        "QDRANT__STORAGE__STORAGE_PATH": str(storage_dir() / "storage"),
        "QDRANT__STORAGE__SNAPSHOTS_PATH": str(storage_dir() / "snapshots"),
        # Local address only. Nothing outside this computer can reach the store,
        # so it needs no password. This follows the same reasoning as the shared
        # embedding model's socket. The dashboard already has an open finding for
        # running without authentication, and this must not add a second one on a
        # wider interface.
        "QDRANT__TELEMETRY_DISABLED": "true",
    })

    # Detached, so it outlives the session that needed it first: the whole point
    # is that several sessions share it.
    creation = {}
    if platform_key()[0] == "windows":  # pragma: no cover - exercised on Windows only
        creation["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        creation["start_new_session"] = True

    with log_file().open("a") as log:
        log.write(f"\n--- mgcp-qdrant start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.flush()
        proc = subprocess.Popen(
            [str(binary_path())],
            cwd=str(storage_dir()),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **creation,
        )
    pid_file().write_text(str(proc.pid))

    if not wait:
        return url

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if is_answering(url):
            if progress:
                print(f"Qdrant {QDRANT_VERSION} listening on {url} (pid {proc.pid})")
            return url
        if proc.poll() is not None:
            tail = ""
            try:
                tail = "\n".join(log_file().read_text().splitlines()[-15:])
            except OSError:
                pass
            raise QdrantServerError(
                f"Qdrant exited immediately (code {proc.returncode}). Last log lines:\n{tail}"
            )
        time.sleep(0.2)

    raise QdrantServerError(
        f"Qdrant did not answer on {url} within {START_TIMEOUT:.0f}s. See {log_file()}."
    )


def stop(progress: bool = True) -> bool:
    """Stop a server we started. True if one was running."""
    pid = running_pid()
    if pid is None:
        if progress:
            print("No MGCP-managed Qdrant is running.")
        pid_file().unlink(missing_ok=True)
        return False

    if platform_key()[0] == "windows":  # pragma: no cover - exercised on Windows only
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        if platform_key()[0] != "windows":
            os.kill(pid, signal.SIGKILL)

    pid_file().unlink(missing_ok=True)
    if progress:
        print(f"Stopped Qdrant (pid {pid}).")
    return True


def ensure_running_if_configured(port: int = DEFAULT_PORT) -> bool:
    """Start the local server when the config points at one that is not up.

    Called before a vector store is opened. A session that finds its configured
    server down would otherwise lose semantic search for the rest of its life;
    with this, the first session to need vectors brings the server up and the
    rest simply connect. Returns True when a server is answering afterwards.

    This stays quiet and does its best. A failure here falls through to the
    caller's existing "vector store unavailable" message, which says what to do.
    """
    url = get_value("qdrant_url")
    if not url:
        return False
    if is_answering(url):
        return True
    if url != default_url(port) or installed_version() is None:
        # Somebody else's server, or none installed: not ours to start.
        return False
    if os.environ.get("MGCP_QDRANT_AUTOSTART", "1").strip() in ("0", "false", "no"):
        return False
    try:
        start(port=port, progress=False)
        return True
    except QdrantServerError as exc:
        logger.warning(f"Could not start the configured local Qdrant: {exc}")
        return False


def status(port: int = DEFAULT_PORT) -> dict:
    """Everything needed to answer "is multi-session on, and working?"."""
    url = default_url(port)
    try:
        configured = get_value("qdrant_url")
        config_error = None
    except ValueError as exc:
        configured, config_error = None, str(exc)
    return {
        "platform": "/".join(platform_key()),
        "binary": str(binary_path()),
        "installed_version": installed_version(),
        "expected_version": QDRANT_VERSION,
        "storage": str(storage_dir()),
        "pid": running_pid(),
        "url": url,
        "answering": is_answering(url),
        "configured_url": configured,
        "config": str(get_data_dir() / "config.json"),
        "config_error": config_error,
        "mode": "server" if configured else "embedded (single session)",
    }


def setup(port: int = DEFAULT_PORT, progress: bool = True) -> dict:
    """Install, start, and point MGCP at it. The one-command path."""
    install(progress=progress)
    url = start(port=port, progress=progress)
    set_value("qdrant_url", url)
    if progress:
        print(f"\nWrote qdrant_url={url} to {get_data_dir() / 'config.json'}")
        print("Every MGCP session on this machine now shares one vector store.")
        print("\nNext: `mgcp-migrate --force` rebuilds the index into the server")
        print("from lessons.db, which stays the source of truth either way.")
    return status(port=port)


def teardown(port: int = DEFAULT_PORT, progress: bool = True) -> None:
    """Stop the server and go back to embedded single-session."""
    stop(progress=progress)
    set_value("qdrant_url", None)
    if progress:
        print("Removed qdrant_url from the config; MGCP is back on embedded Qdrant.")
        print(f"The server's data is still at {storage_dir()} if you want it back.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mgcp-qdrant",
        description=(
            "Run a local Qdrant server so several MGCP sessions can share one "
            "vector store. Embedded Qdrant allows a single client per directory; "
            "single-session MGCP needs none of this."
        ),
    )
    parser.add_argument(
        "command",
        choices=["setup", "install", "start", "stop", "status", "teardown"],
        help=(
            "setup: install + start + configure (start here). install: download the "
            "binary. start/stop: supervise it. status: what is installed, running "
            "and configured. teardown: stop it and return to embedded."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--force", action="store_true", help="reinstall even if present")
    parser.add_argument("--json", action="store_true", help="machine-readable status")
    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            result = setup(port=args.port, progress=not args.json)
            if args.json:
                print(json.dumps(result, indent=2))
        elif args.command == "install":
            install(force=args.force)
        elif args.command == "start":
            start(port=args.port)
        elif args.command == "stop":
            stop()
        elif args.command == "teardown":
            teardown(port=args.port)
        else:
            print(json.dumps(status(port=args.port), indent=2))
    except QdrantServerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
