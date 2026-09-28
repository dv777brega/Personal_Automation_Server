"""Exercise filesystem automations with disposable data, never the user's folders."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from zipfile import ZipFile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def execute(name, *arguments):
    return subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, arguments)],
                          capture_output=True, text=True, timeout=10)


def test_disk_space_success_and_threshold(tmp_path):
    result = execute("disk_space.py", "--path", tmp_path, "--min-free-gb", "0")
    assert result.returncode == 0 and "Disk space check passed" in result.stdout
    result = execute("disk_space.py", "--path", tmp_path, "--min-free-gb", "999999999")
    assert result.returncode == 1 and "Low disk space" in result.stderr
    assert execute("disk_space.py", "--path", tmp_path / "missing").returncode != 0


def test_backup_round_trip_unique_archives_and_invalid_destination(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "backups"
    (source / "nested").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "nested" / "hello.txt").write_text("important data", encoding="utf-8")
    for _ in range(2):
        result = execute("backup_folder.py", "--source", source, "--destination", destination)
        assert result.returncode == 0, result.stderr
    archives = list(destination.glob("*.zip"))
    assert len(archives) == 2
    with ZipFile(archives[0]) as archive:
        assert archive.read("nested/hello.txt") == b"important data"
        assert "empty/" in archive.namelist()
    assert (source / "nested" / "hello.txt").read_text() == "important data"
    assert not list(destination.glob("*.partial"))
    rejected = execute("backup_folder.py", "--source", source, "--destination", source / "backups")
    assert rejected.returncode != 0 and "outside" in rejected.stderr
    assert not (source / "backups").exists()


def test_organizer_preview_apply_collision_and_partial_files(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"report")
    (tmp_path / "photo.PNG").write_bytes(b"photo")
    (tmp_path / "pending.crdownload").write_bytes(b"partial")
    (tmp_path / "recent.txt").write_bytes(b"recent")
    for name in ("report.pdf", "photo.PNG"):
        os.utime(tmp_path / name, (1, 1))
    preview = execute("organize_downloads.py", "--folder", tmp_path)
    assert preview.returncode == 0 and "Would move: 2" in preview.stdout
    assert (tmp_path / "report.pdf").exists() and not (tmp_path / "Documents").exists()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "report.pdf").write_bytes(b"existing report")
    applied = execute("organize_downloads.py", "--folder", tmp_path, "--apply")
    assert applied.returncode == 0, applied.stderr
    assert (tmp_path / "Images" / "photo.PNG").read_bytes() == b"photo"
    assert not (tmp_path / "photo.PNG").exists()
    assert (tmp_path / "Documents" / "report.pdf").read_bytes() == b"existing report"
    assert (tmp_path / "report.pdf").read_bytes() == b"report"
    assert (tmp_path / "pending.crdownload").exists() and (tmp_path / "recent.txt").exists()


def test_organizer_rejects_linked_category(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "report.pdf").write_bytes(b"report")
    try:
        (folder / "Documents").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this system")
    result = execute("organize_downloads.py", "--folder", folder, "--apply", "--min-age-seconds", "0")
    assert result.returncode == 1
    assert (folder / "report.pdf").exists() and not list(outside.iterdir())


def test_websites_validates_every_url_before_opening():
    spec = importlib.util.spec_from_file_location("open_websites", SCRIPTS / "open_websites.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(sys, "argv", ["open_websites.py", "https://example.com", "file:///C:/secret"]), patch.object(module.webbrowser, "open_new_tab") as browser:
        with pytest.raises(SystemExit):
            module.main()
        browser.assert_not_called()
    with patch.object(sys, "argv", ["open_websites.py", "https://example.com"]), patch.object(module.webbrowser, "open_new_tab", return_value=True) as browser:
        module.main()
        browser.assert_called_once_with("https://example.com")
