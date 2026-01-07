"""Backup and restore MGCP data."""

import shutil
import sys
from datetime import datetime
from pathlib import Path

# Default data directory
DEFAULT_DATA_DIR = Path.home() / ".mgcp"


def backup(output_path: Path | None = None, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """
    Create a backup of the MGCP data directory.

    Args:
        output_path: Where to save the backup (default: mgcp-backup-YYYYMMDD-HHMMSS.tar.gz)
        data_dir: MGCP data directory to backup

    Returns:
        Path to the created backup file
    """
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = Path.cwd() / f"mgcp-backup-{timestamp}"

    # Create archive
    archive_path = shutil.make_archive(
        str(output_path),
        "gztar",
        root_dir=data_dir.parent,
        base_dir=data_dir.name,
    )

    return Path(archive_path)


def restore(backup_path: Path, data_dir: Path = DEFAULT_DATA_DIR, force: bool = False) -> None:
    """
    Restore MGCP data from a backup.

    Args:
        backup_path: Path to the backup archive
        data_dir: Where to restore (default: ~/.mgcp)
        force: Overwrite existing data without prompting
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    if data_dir.exists() and not force:
        raise FileExistsError(
            f"Data directory already exists: {data_dir}\n"
            "Use --force to overwrite, or backup existing data first."
        )

    # Remove existing if force
    if data_dir.exists() and force:
        shutil.rmtree(data_dir)

    # Extract archive
    shutil.unpack_archive(backup_path, data_dir.parent)

    # Handle case where archive contains .mgcp subdirectory
    extracted = data_dir.parent / ".mgcp"
    if extracted != data_dir and extracted.exists():
        if data_dir.exists():
            shutil.rmtree(data_dir)
        extracted.rename(data_dir)


def main():
    """CLI entry point for mgcp-backup."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backup and restore MGCP data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mgcp-backup                     Create backup in current directory
  mgcp-backup -o my-backup.tar.gz Create backup with specific name
  mgcp-backup --restore backup.tar.gz  Restore from backup
  mgcp-backup --list              Show what would be backed up
""",
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output path for backup (default: mgcp-backup-TIMESTAMP.tar.gz)",
    )
    parser.add_argument(
        "--restore",
        type=Path,
        metavar="BACKUP",
        help="Restore from a backup file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing data when restoring",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List files that would be backed up",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"MGCP data directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version="mgcp-backup 1.0.0",
    )

    args = parser.parse_args()

    try:
        if args.list:
            # List contents
            if not args.data_dir.exists():
                print(f"Data directory not found: {args.data_dir}")
                sys.exit(1)

            print(f"MGCP data directory: {args.data_dir}\n")
            total_size = 0
            for item in args.data_dir.rglob("*"):
                if item.is_file():
                    size = item.stat().st_size
                    total_size += size
                    rel_path = item.relative_to(args.data_dir)
                    print(f"  {rel_path} ({size:,} bytes)")

            print(f"\nTotal: {total_size:,} bytes ({total_size / 1024 / 1024:.1f} MB)")

        elif args.restore:
            # Restore from backup
            print(f"Restoring from: {args.restore}")
            restore(args.restore, args.data_dir, args.force)
            print(f"Restored to: {args.data_dir}")

        else:
            # Create backup
            if not args.data_dir.exists():
                print(f"Data directory not found: {args.data_dir}")
                print("Nothing to backup. Run mgcp-bootstrap first.")
                sys.exit(1)

            archive = backup(args.output, args.data_dir)
            size = archive.stat().st_size
            print(f"Backup created: {archive}")
            print(f"Size: {size:,} bytes ({size / 1024 / 1024:.1f} MB)")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except FileExistsError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()