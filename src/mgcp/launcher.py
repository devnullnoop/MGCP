"""Unified launcher for MGCP services."""

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_web_server(host: str, port: int):
    """Run the web server for telemetry visualization."""
    import uvicorn
    from .web_server import app

    uvicorn.run(app, host=host, port=port, log_level="warning")


def run_mcp_server():
    """Run the MCP server (stdio transport)."""
    from .server import main as mcp_main
    mcp_main()


def open_dashboard(port: int, delay: float = 2.0):
    """Open the dashboard in a browser after a delay."""
    import time
    time.sleep(delay)
    url = f"http://127.0.0.1:{port}"
    logger.info(f"Opening dashboard: {url}")
    webbrowser.open(url)


def main():
    """Main entry point for the launcher."""
    parser = argparse.ArgumentParser(
        description="MGCP - Memory Graph Control Protocol Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  dashboard    Start the web dashboard for visualization (default)
  mcp          Start the MCP server (for Claude Code integration)
  all          Start both dashboard and MCP server
  bootstrap    Seed the database with initial lessons
  status       Show system status

Examples:
  mgcp-launcher dashboard --port 8765
  mgcp-launcher mcp
  mgcp-launcher all --dashboard-port 8765
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Dashboard command
    dash_parser = subparsers.add_parser("dashboard", help="Start web dashboard")
    dash_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    dash_parser.add_argument("--port", type=int, default=8765, help="Port to bind to")
    dash_parser.add_argument("--no-browser", action="store_true", help="Don't open browser")

    # MCP command
    mcp_parser = subparsers.add_parser("mcp", help="Start MCP server")

    # All command
    all_parser = subparsers.add_parser("all", help="Start dashboard and MCP server")
    all_parser.add_argument("--host", default="127.0.0.1", help="Dashboard host")
    all_parser.add_argument("--dashboard-port", type=int, default=8765, help="Dashboard port")
    all_parser.add_argument("--no-browser", action="store_true", help="Don't open browser")

    # Bootstrap command
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Seed initial lessons")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show system status")

    args = parser.parse_args()

    if args.command is None:
        # Default to dashboard
        args.command = "dashboard"
        args.host = "127.0.0.1"
        args.port = 8765
        args.no_browser = False

    if args.command == "dashboard":
        logger.info(f"Starting dashboard on http://{args.host}:{args.port}")

        if not args.no_browser:
            browser_thread = threading.Thread(
                target=open_dashboard,
                args=(args.port,),
                daemon=True,
            )
            browser_thread.start()

        run_web_server(args.host, args.port)

    elif args.command == "mcp":
        logger.info("Starting MCP server (stdio transport)")
        run_mcp_server()

    elif args.command == "all":
        logger.info("Starting both dashboard and MCP server")
        logger.info(f"Dashboard will be available at http://{args.host}:{args.dashboard_port}")

        # Start dashboard in a separate process
        dashboard_cmd = [
            sys.executable, "-m", "mgcp.launcher",
            "dashboard",
            "--host", args.host,
            "--port", str(args.dashboard_port),
        ]
        if args.no_browser:
            dashboard_cmd.append("--no-browser")

        dashboard_proc = subprocess.Popen(dashboard_cmd)

        def cleanup(sig, frame):
            logger.info("Shutting down...")
            dashboard_proc.terminate()
            sys.exit(0)

        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

        # Run MCP server in main process
        run_mcp_server()

    elif args.command == "bootstrap":
        from .bootstrap import main as bootstrap_main
        bootstrap_main()

    elif args.command == "status":
        asyncio.run(show_status())


async def show_status():
    """Show current system status."""
    from .persistence import LessonStore
    from .telemetry import TelemetryLogger
    from .vector_store import VectorStore

    print("\n" + "=" * 50)
    print("  MGCP System Status")
    print("=" * 50 + "\n")

    # Check lesson store
    try:
        store = LessonStore()
        lessons = await store.get_all_lessons()
        print(f"  Lessons in database: {len(lessons)}")

        # Count by category
        categories = {}
        for lesson in lessons:
            cat = lesson.parent_id or "root"
            categories[cat] = categories.get(cat, 0) + 1
        print(f"  Categories: {len(categories)}")
    except Exception as e:
        print(f"  Lesson store: ERROR - {e}")

    # Check vector store
    try:
        vector_store = VectorStore()
        ids = vector_store.get_all_ids()
        print(f"  Vectors in ChromaDB: {len(ids)}")
    except Exception as e:
        print(f"  Vector store: ERROR - {e}")

    # Check telemetry
    try:
        telemetry = TelemetryLogger()
        sessions = await telemetry.get_session_history(limit=10)
        print(f"  Recent sessions: {len(sessions)}")

        usage = await telemetry.get_lesson_usage()
        total_retrievals = sum(u.get("total_retrievals", 0) for u in usage)
        print(f"  Total retrievals: {total_retrievals}")
    except Exception as e:
        print(f"  Telemetry: ERROR - {e}")

    # Check paths
    print("\n  Paths:")
    from pathlib import Path
    data_dir = Path.home() / ".mgcp"
    print(f"    Data directory: {data_dir}")
    print(f"    Exists: {data_dir.exists()}")

    if data_dir.exists():
        files = list(data_dir.glob("*"))
        for f in files:
            size = f.stat().st_size if f.is_file() else 0
            print(f"      {f.name}: {size:,} bytes")

    print("\n" + "=" * 50 + "\n")


if __name__ == "__main__":
    main()