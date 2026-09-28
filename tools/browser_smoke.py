"""Optional real-browser smoke test; uses an isolated temporary database.

Install playwright and its Chromium browser before running this script.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(tempfile.gettempdir())


@contextmanager
def server(database, token=""):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    scripts = database.parent / (database.stem + "-scripts")
    shutil.copytree(ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "_browser_wait.py").write_text("import time\ntime.sleep(30)\n")
    environment = {**os.environ, "PAS_DATABASE_PATH": str(database), "PAS_API_TOKEN": token,
                   "PAS_ALLOWED_HOSTS": "localhost,127.0.0.1", "PAS_SCRIPTS_DIR": str(scripts)}
    with tempfile.TemporaryFile() as logs:
        process = subprocess.Popen([sys.executable, "-m", "backend.app", "--port", str(port)],
                                   cwd=ROOT, env=environment, stdout=logs, stderr=logs)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                try:
                    with urlopen(url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if process.poll() is not None:
                        logs.seek(0)
                        raise RuntimeError(logs.read().decode())
                    time.sleep(0.1)
            else:
                raise RuntimeError("Server did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    with tempfile.TemporaryDirectory(prefix="pas-browser-") as directory, sync_playwright() as playwright:
        root = Path(directory)
        browser = playwright.chromium.launch()
        errors = []
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        with server(root / "normal.db") as url:
            page.goto(url)
            expect(page.locator("#connection")).to_have_text("Server online")
            expect(page.locator("#script")).to_have_value("disk_space.py")
            page.locator("#arguments").fill('["--min-free-gb", "0"]')
            page.locator("#run-button").click()
            expect(page.locator("#task-dialog")).to_be_visible()
            expect(page.locator("#task-meta .badge")).to_have_text("COMPLETED", timeout=15000)
            expect(page.locator("#task-stdout")).to_contain_text("Disk space check passed.")
            page.locator("#rerun-task").click()
            expect(page.locator("#task-number")).to_have_text("TASK #2")
            expect(page.locator("#task-meta .badge")).to_have_text("COMPLETED", timeout=10000)
            page.locator("#close-dialog").click()
            page.locator("#script").select_option("_browser_wait.py")
            page.locator("#arguments").fill('[]')
            page.locator("#run-button").click()
            expect(page.locator("#task-number")).to_have_text("TASK #3")
            page.locator("#cancel-task").click()
            expect(page.locator("#task-meta .badge")).to_have_text("CANCELLED", timeout=10000)
            page.locator("#close-dialog").click()
            (root / "normal-scripts" / "_browser_wait.py").unlink()
            page.locator("#script").select_option("disk_space.py")
            page.locator("#arguments").fill('["--min-free-gb", "0"]')
            page.locator("#mode").select_option("schedule")
            page.locator("#schedule-name").fill("Disk space check")
            page.locator("#interval").fill("10")
            page.locator("#run-button").click()
            expect(page.locator("#schedule-list")).to_contain_text("Disk space check")
            expect(page.locator("#stat-total")).to_have_text("4", timeout=20000)
            page.get_by_role("button", name="Pause", exact=True).click()
            expect(page.locator("#schedule-list")).to_contain_text("Paused")
            page.locator("#status-filter").select_option("COMPLETED")
            expect(page.locator("#tasks")).to_contain_text("disk_space.py")
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            page.screenshot(path=str(root / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
                .filter(el => el.getBoundingClientRect().right > window.innerWidth && getComputedStyle(el).position !== 'absolute')
                .map(el => ({element: el.tagName + '.' + el.className, right: el.getBoundingClientRect().right}))""")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Mobile layout overflows: {overflow}"
            page.screenshot(path=str(root / "mobile.png"), full_page=True)
            page.on("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Delete", exact=True).click()
            expect(page.locator("#schedules-empty")).to_be_visible()
            assert not errors, errors
            print("PASS: run, output, rerun, cancel, schedule dispatch/pause/delete, filtering, mobile layout")
            # Keep review artifacts outside the repository.
            import shutil
            shutil.copy(root / "desktop.png", ARTIFACTS / "pas-desktop.png")
            shutil.copy(root / "mobile.png", ARTIFACTS / "pas-mobile.png")
        with server(root / "auth.db", token="browser-test-token") as url:
            page.goto(url)
            expect(page.locator("#auth-panel")).to_be_visible()
            expect(page.locator("#workspace")).to_be_hidden()
            page.locator("#token").fill("browser-test-token")
            page.get_by_role("button", name="Connect", exact=True).click()
            expect(page.locator("#workspace")).to_be_visible()
            expect(page.locator("#connection")).to_have_text("Server online")
            page.get_by_role("button", name="Lock", exact=True).click()
            expect(page.locator("#workspace")).to_be_hidden()
            print("PASS: token unlock and lock")
        browser.close()
        print(f"Screenshots: {ARTIFACTS / 'pas-desktop.png'} and {ARTIFACTS / 'pas-mobile.png'}")


if __name__ == "__main__":
    main()
