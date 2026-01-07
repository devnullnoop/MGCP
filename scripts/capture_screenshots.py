#!/usr/bin/env python3
"""Capture screenshots of the MGCP web dashboard for documentation.

Prerequisites:
    pip install playwright
    playwright install chromium

Usage:
    1. Seed demo data: python scripts/seed_demo_data.py
    2. Start web server: python -m mgcp.web_server (in another terminal)
    3. Run this script: python scripts/capture_screenshots.py

Screenshots are saved to docs/screenshots/
"""

import asyncio
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright not installed.")
    print("   Install with: pip install playwright && playwright install chromium")
    sys.exit(1)


SCREENSHOTS_DIR = Path(__file__).parent.parent / "docs" / "screenshots"
BASE_URL = "http://localhost:8765"

# Screenshot configurations
SCREENSHOTS = [
    {
        "name": "dashboard",
        "url": "/",
        "description": "Main dashboard with lesson graph visualization",
        "wait_for": "#graph-container svg",  # Wait for graph to render
        "delay": 2000,  # Extra delay for animations
        "viewport": {"width": 1400, "height": 900},
    },
    {
        "name": "lessons",
        "url": "/lessons",
        "description": "Lesson management interface",
        "wait_for": ".lesson-card, .lesson-item, #lessons-container",
        "delay": 500,
        "viewport": {"width": 1200, "height": 800},
    },
    {
        "name": "projects",
        "url": "/projects",
        "description": "Project catalogue and context management",
        "wait_for": ".project-card, #projects-container",
        "delay": 500,
        "viewport": {"width": 1200, "height": 900},
    },
    {
        "name": "graph-detail",
        "url": "/",
        "description": "Close-up of the knowledge graph",
        "wait_for": "#graph-container svg",
        "delay": 2500,
        "viewport": {"width": 1000, "height": 700},
        "clip": {"x": 0, "y": 0, "width": 700, "height": 600},  # Crop to graph area
    },
]


async def check_server_running():
    """Check if the web server is running."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/api/health", timeout=aiohttp.ClientTimeout(total=2)) as resp:
                return resp.status == 200
    except Exception:
        return False


async def capture_screenshots():
    """Capture all screenshots."""
    print("📸 MGCP Screenshot Capture")
    print("=" * 40)

    # Check server
    print("\n🔍 Checking web server...")
    if not await check_server_running():
        print("❌ Web server not running at", BASE_URL)
        print("   Start it with: python -m mgcp.web_server")
        return False

    print("✓ Web server is running")

    # Ensure output directory exists
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Launch browser
        print("\n🌐 Launching browser...")
        browser = await p.chromium.launch()

        for config in SCREENSHOTS:
            name = config["name"]
            print(f"\n📷 Capturing: {name}")
            print(f"   {config['description']}")

            # Create context with viewport
            context = await browser.new_context(
                viewport=config["viewport"],
                device_scale_factor=2,  # Retina quality
            )
            page = await context.new_page()

            try:
                # Navigate to page
                url = f"{BASE_URL}{config['url']}"
                await page.goto(url, wait_until="networkidle")

                # Wait for specific element
                if config.get("wait_for"):
                    try:
                        await page.wait_for_selector(config["wait_for"], timeout=5000)
                    except Exception:
                        print(f"   ⚠️  Selector not found: {config['wait_for']}")

                # Extra delay for animations/rendering
                if config.get("delay"):
                    await asyncio.sleep(config["delay"] / 1000)

                # Capture screenshot
                screenshot_path = SCREENSHOTS_DIR / f"{name}.png"
                screenshot_options = {"path": str(screenshot_path)}

                if config.get("clip"):
                    screenshot_options["clip"] = config["clip"]

                await page.screenshot(**screenshot_options)
                print(f"   ✓ Saved: {screenshot_path.name}")

            except Exception as e:
                print(f"   ❌ Error: {e}")

            finally:
                await context.close()

        await browser.close()

    print("\n" + "=" * 40)
    print("✅ Screenshots saved to:", SCREENSHOTS_DIR)
    print("\nAdd to README.md with:")
    print('  ![Dashboard](docs/screenshots/dashboard.png)')
    print('  ![Lessons](docs/screenshots/lessons.png)')
    print('  ![Projects](docs/screenshots/projects.png)')

    return True


async def main():
    """Main entry point."""
    try:
        success = await capture_screenshots()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nCancelled.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())