"""Sort completed downloads into folders by file type. Preview unless --apply is supplied."""
import argparse
import os
from pathlib import Path
import shutil
import time

CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Archives": {".zip", ".7z", ".rar", ".tar", ".gz"},
    "Installers": {".exe", ".msi", ".msix"},
}
INCOMPLETE = {".part", ".crdownload", ".download", ".tmp", ".partial"}


def move_without_overwrite(source, destination):
    """Reserve the target exclusively; never replace an existing file."""
    before = source.stat()
    with destination.open("xb") as output:
        try:
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise OSError("Source changed while copying; left original in place")
        except BaseException:
            output.close()
            destination.unlink(missing_ok=True)
            raise
    try:
        shutil.copystat(source, destination)
        source.unlink()
    except OSError:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", default=str(Path.home() / "Downloads"), help="Downloads folder to organize")
    parser.add_argument("--apply", action="store_true", help="Actually move files; default is preview only")
    parser.add_argument("--min-age-seconds", type=int, default=60, help="Skip recently modified files (default: 60)")
    args = parser.parse_args()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        parser.error(f"Folder does not exist: {folder}. Supply --folder with the correct location.")
    if args.min_age_seconds < 0:
        parser.error("--min-age-seconds must be nonnegative")
    print(f"{'APPLY' if args.apply else 'PREVIEW'}: {folder}")
    changed = skipped = errors = 0
    for source in sorted(folder.iterdir()):
        try:
            if source.is_symlink() or not source.is_file() or source.name.startswith(".") or source.suffix.lower() in INCOMPLETE:
                continue
            if time.time() - source.stat().st_mtime < args.min_age_seconds:
                skipped += 1
                continue
            category = next((name for name, extensions in CATEGORIES.items() if source.suffix.lower() in extensions), "Other")
            target_folder = folder / category
            destination = target_folder / source.name
            if target_folder.is_symlink() or target_folder.resolve() != target_folder.absolute():
                raise OSError(f"Refusing linked category folder: {target_folder}")
            if destination.exists() or destination.is_symlink():
                print(f"Skipped existing destination: {category}/{source.name}")
                skipped += 1
                continue
            if args.apply:
                target_folder.mkdir(exist_ok=True)
                move_without_overwrite(source, destination)
            print(f"{source.name} -> {category}/{source.name}")
            changed += 1
        except OSError as exc:
            print(f"Could not organize {source.name}: {exc}")
            errors += 1
    print(f"{'Moved' if args.apply else 'Would move'}: {changed}; skipped: {skipped}; errors: {errors}")
    if not args.apply:
        print("Preview only. Add --apply to move these files.")
    if errors:
        parser.exit(1, "Some files could not be organized. Review the output before retrying.\n")


if __name__ == "__main__":
    main()
