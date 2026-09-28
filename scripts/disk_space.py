"""Check free disk space. Works on Windows and other operating systems."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(Path.home()), help="Folder or drive to check, e.g. C:\\")
    parser.add_argument("--min-free-gb", type=float, default=10, help="Fail if less than this many GiB are free")
    args = parser.parse_args()
    if args.min_free_gb < 0:
        parser.error("--min-free-gb must be nonnegative")
    path = Path(args.path).expanduser().resolve()
    try:
        total, used, free = shutil.disk_usage(path)
    except OSError as exc:
        parser.exit(1, f"Cannot check {path}: {exc}\n")
    gib = 1024 ** 3
    print(f"Disk space for: {path}")
    print(f"Total: {total / gib:.2f} GiB")
    print(f"Used:  {used / gib:.2f} GiB")
    print(f"Free:  {free / gib:.2f} GiB ({free / total:.1%})")
    if free / gib < args.min_free_gb:
        parser.exit(1, f"Low disk space: less than {args.min_free_gb:g} GiB available.\n")
    print("Disk space check passed.")


if __name__ == "__main__":
    main()
