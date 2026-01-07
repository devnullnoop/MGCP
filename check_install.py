#!/usr/bin/env python3
"""Pre-flight checks for MGCP installation.

Run this BEFORE `pip install -e .` to catch common issues:
    python check_install.py

Or run installation with checks:
    python check_install.py --install
"""

import subprocess
import sys
from pathlib import Path


def get_pip_version():
    """Get the current pip version as a tuple."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
        )
        # Output: "pip 21.3.1 from /path/to/pip (python 3.11)"
        version_str = result.stdout.split()[1]
        parts = version_str.split(".")
        return tuple(int(p) for p in parts[:2])
    except Exception:
        return (0, 0)


def check_python_version():
    """Check Python version is 3.11-3.13 (best compatibility)."""
    version = sys.version_info
    if version < (3, 11):
        return False, f"Python 3.11+ required, found {version.major}.{version.minor}"
    if version >= (3, 14):
        return False, f"Python 3.14+ not yet supported (found {version.major}.{version.minor}) - use Python 3.11, 3.12, or 3.13"
    return True, f"Python {version.major}.{version.minor}.{version.micro}"


def check_pip_version():
    """Check pip version supports PEP 660 editable installs."""
    version = get_pip_version()
    if version < (21, 3):
        return False, f"pip 21.3+ required for editable installs, found {version[0]}.{version[1]}"
    return True, f"pip {version[0]}.{version[1]}"


def check_venv():
    """Check if running in a virtual environment."""
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        return False, "Not in a virtual environment (recommended)"
    return True, f"Virtual environment: {sys.prefix}"


def check_pyproject():
    """Check pyproject.toml exists."""
    if not Path("pyproject.toml").exists():
        return False, "pyproject.toml not found - run from MGCP directory"
    return True, "pyproject.toml found"


def upgrade_pip():
    """Upgrade pip, setuptools, and wheel to latest versions."""
    print("\n📦 Upgrading pip, setuptools, and wheel...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        new_version = get_pip_version()
        print(f"   ✅ Upgraded to pip {new_version[0]}.{new_version[1]}")
        return True
    else:
        print(f"   ❌ Failed: {result.stderr}")
        return False


def install_numpy_first():
    """Install numpy explicitly first to avoid build issues."""
    print("\n📦 Pre-installing numpy (avoids build issues on Intel Macs)...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "numpy>=1.26.0,<2.0"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("   ✅ numpy installed")
        return True
    else:
        print(f"   ❌ numpy install failed: {result.stderr}")
        return False


def install_mgcp(dev=True):
    """Install MGCP in editable mode."""
    print("\n📦 Installing MGCP...")
    cmd = [sys.executable, "-m", "pip", "install", "-e", ".[dev]" if dev else "."]
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def run_bootstrap():
    """Run mgcp-bootstrap to seed initial lessons."""
    print("\n🌱 Seeding initial lessons...")
    result = subprocess.run(["mgcp-bootstrap"], capture_output=False)
    return result.returncode == 0


def main():
    print("🔍 MGCP Installation Pre-flight Checks\n")
    print("=" * 50)

    checks = [
        ("Python version", check_python_version),
        ("pip version", check_pip_version),
        ("Virtual environment", check_venv),
        ("Project files", check_pyproject),
    ]

    all_passed = True
    issues = []

    for name, check_fn in checks:
        passed, message = check_fn()
        status = "✅" if passed else "❌"
        print(f"{status} {name}: {message}")
        if not passed:
            all_passed = False
            issues.append((name, message, check_fn))

    print("=" * 50)

    if all_passed:
        print("\n✅ All checks passed! Ready to install.\n")
    else:
        print("\n⚠️  Some checks failed. Here's how to fix them:\n")

        for name, message, check_fn in issues:
            if check_fn == check_python_version:
                print(f"  • {name}: Install Python 3.11 or later")
                print("    https://www.python.org/downloads/")
            elif check_fn == check_pip_version:
                print(f"  • {name}: Run `pip install --upgrade pip`")
            elif check_fn == check_venv:
                print(f"  • {name}: Create a virtual environment:")
                print("    python3 -m venv .venv")
                print("    source .venv/bin/activate")
            elif check_fn == check_pyproject:
                print(f"  • {name}: cd into the MGCP directory first")

    # Handle --install flag
    if "--install" in sys.argv:
        if not all_passed:
            # Try to auto-fix pip version
            pip_ok, _ = check_pip_version()
            if not pip_ok:
                print("\n🔧 Attempting to fix pip version...")
                if upgrade_pip():
                    pip_ok = True

            # Re-check after fixes
            python_ok, _ = check_python_version()
            venv_ok, _ = check_venv()
            pyproject_ok, _ = check_pyproject()

            if not python_ok:
                print("\n❌ Cannot proceed: Python 3.11+ required")
                sys.exit(1)
            if not pyproject_ok:
                print("\n❌ Cannot proceed: Run from MGCP directory")
                sys.exit(1)
            if not venv_ok:
                print("\n⚠️  Warning: Installing without virtual environment")
                response = input("   Continue anyway? [y/N]: ")
                if response.lower() != "y":
                    sys.exit(1)

        # Always upgrade pip/setuptools/wheel first
        upgrade_pip()

        # Pre-install numpy to avoid build issues
        if not install_numpy_first():
            version = sys.version_info
            print(f"\n❌ numpy installation failed on Python {version.major}.{version.minor}")
            if version >= (3, 13):
                print("   Python 3.13 requires numpy 2.0+, which may conflict with onnxruntime.")
                print("   Recommended: Use Python 3.11 or 3.12 for best compatibility.")
            sys.exit(1)

        # Proceed with installation
        if install_mgcp():
            print("\n✅ MGCP installed successfully!")
            run_bootstrap()
            print("\n🎉 Installation complete! Next steps:")
            print("   1. Run: mgcp-init")
            print("   2. Restart your LLM client")
            print("   3. Start using MGCP!")
        else:
            print("\n❌ Installation failed. Check the errors above.")
            sys.exit(1)
    else:
        if all_passed:
            print("Run with --install to proceed:")
            print("  python check_install.py --install")
            print("\nOr install manually:")
            print("  pip install -e '.[dev]'")
            print("  mgcp-bootstrap")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
