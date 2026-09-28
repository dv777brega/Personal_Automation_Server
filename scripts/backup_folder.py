"""Create a dated ZIP backup of a folder without changing the source files."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import uuid
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Folder to back up")
    parser.add_argument("--destination", default=str(Path.home() / "AutomationBackups"), help="Folder for ZIP backups")
    args = parser.parse_args()
    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()
    if not source.is_dir():
        parser.error(f"Source folder does not exist: {source}")
    if destination == source or source in destination.parents:
        parser.error("The backup destination must be outside the source folder")
    name = f"{source.name or 'drive'}_{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:8]}.zip"
    archive = destination / name
    partial = destination / (name + ".partial")
    count = skipped = 0
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with ZipFile(partial, "x", compression=ZIP_DEFLATED) as backup:
            def walk_error(error):
                raise error

            for root, directories, files in os.walk(source, followlinks=False, onerror=walk_error):
                parent = Path(root)
                # Avoid Windows junctions as well as symlinks into other folders.
                for directory in directories[:]:
                    child = parent / directory
                    if child.is_symlink() or child.resolve() != child.absolute() or os.path.ismount(child):
                        directories.remove(directory)
                        skipped += 1
                for filename in files:
                    path = parent / filename
                    if path.is_symlink() or not path.is_file():
                        skipped += 1
                        continue
                    backup.write(path, path.relative_to(source))
                    count += 1
                if parent != source and not directories and not files:
                    backup.write(parent, str(parent.relative_to(source)) + "/")
        # The random suffix makes each backup independent; publish only after ZIP finalization.
        partial.rename(archive)
    except (OSError, ValueError) as exc:
        partial.unlink(missing_ok=True)
        parser.exit(1, f"Backup failed: {exc}\n")
    print(f"Backup created: {archive}")
    print(f"Files copied: {count}; links/special entries skipped: {skipped}")
    print("Original files were left in place.")


if __name__ == "__main__":
    main()
