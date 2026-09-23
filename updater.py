"""
updater.py — Fetchly auto-update engine
========================================
Checks GitHub for a newer version.json, downloads the repo ZIP,
extracts changed files, and restarts the app.

Usage (called by native_app.py):
    from updater import FetchlyUpdater
    u = FetchlyUpdater(progress_cb=my_fn)
    info = u.check()          # → dict with update_available, versions, changelog
    u.install(on_done=cb)     # downloads, replaces, restarts (background thread)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional
import urllib.request
import urllib.error

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────

GITHUB_USER   = "Teddysha180"
GITHUB_REPO   = "Fetchly"
BRANCH        = "master"

RAW_VERSION_URL = (
    f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}"
    f"/{BRANCH}/version.json"
)
ZIP_URL = (
    f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"
    f"/archive/refs/heads/{BRANCH}.zip"
)

# Files/folders the updater will NEVER overwrite (user data / platform assets)
SKIP_PATHS = {
    "logo/bg.png",
    "logo/splash.png",
    ".gitignore",
    ".gitkeep",
}

# The version.json file in the local install
# When frozen by PyInstaller, sys._MEIPASS holds the bundle dir;
# otherwise fall back to the directory of this file.
def _app_base() -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    return Path(__file__).parent

LOCAL_VERSION_FILE = _app_base() / "version.json"

# ─────────────────────────────────────────────
#  SEMVER COMPARE
# ─────────────────────────────────────────────

def _parse_version(v: str) -> tuple[int, ...]:
    """Parse 'MAJOR.MINOR.PATCH' into a comparable tuple of ints."""
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


# ─────────────────────────────────────────────
#  LOCAL VERSION
# ─────────────────────────────────────────────

def get_local_version() -> str:
    try:
        with open(LOCAL_VERSION_FILE, encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


# ─────────────────────────────────────────────
#  UPDATER CLASS
# ─────────────────────────────────────────────

class FetchlyUpdater:
    """
    Thread-safe Fetchly self-updater.

    Parameters
    ----------
    progress_cb : callable(pct: int, msg: str) | None
        Receives download / install progress updates.
        Called from a background thread — must be thread-safe.
    """

    def __init__(self, progress_cb: Optional[Callable[[int, str], None]] = None):
        self._cb        = progress_cb
        self._lock      = threading.Lock()
        self._running   = False

    # ── Public API ────────────────────────────────────────────────────────

    def check(self) -> dict:
        """
        Fetch remote version.json and compare with local version.
        Returns a dict:
            {
              "status":           "ok" | "error",
              "current_version":  str,
              "latest_version":   str,
              "update_available": bool,
              "changelog":        str,
              "error":            str | None,
            }
        """
        local_ver = get_local_version()
        result = {
            "status":           "ok",
            "current_version":  local_ver,
            "latest_version":   local_ver,
            "update_available": False,
            "changelog":        "",
            "error":            None,
        }
        try:
            req = urllib.request.Request(
                RAW_VERSION_URL,
                headers={"Cache-Control": "no-cache", "User-Agent": "Fetchly-Updater/1"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            remote_ver = data.get("version", "0.0.0")
            changelog  = data.get("changelog", "")

            result["latest_version"]   = remote_ver
            result["changelog"]        = changelog
            result["update_available"] = _is_newer(remote_ver, local_ver)

        except urllib.error.URLError as e:
            result["status"] = "error"
            result["error"]  = f"Network error: {e.reason}"
        except Exception as e:
            result["status"] = "error"
            result["error"]  = str(e)

        return result

    def install(self, on_done: Optional[Callable[[bool, str], None]] = None) -> None:
        """
        Start the update in a background daemon thread.
        on_done(success: bool, message: str) is called when finished.
        """
        with self._lock:
            if self._running:
                return
            self._running = True

        t = threading.Thread(target=self._install_worker, args=(on_done,), daemon=True)
        t.start()

    # ── Internal ──────────────────────────────────────────────────────────

    def _progress(self, pct: int, msg: str) -> None:
        if self._cb:
            try:
                self._cb(pct, msg)
            except Exception:
                pass

    def _install_worker(self, on_done: Optional[Callable[[bool, str], None]]) -> None:
        try:
            success, msg = self._do_install()
        except Exception as ex:
            success, msg = False, str(ex)
        finally:
            with self._lock:
                self._running = False

        if on_done:
            try:
                on_done(success, msg)
            except Exception:
                pass

        if success:
            # Give the UI 1.5 s to show the "Restarting…" state, then restart
            time.sleep(1.5)
            _restart_app()

    def _do_install(self) -> tuple[bool, str]:
        install_root = Path(__file__).parent.resolve()

        # ── Step 1: Download ZIP ──────────────────────────────────────────
        self._progress(5, "Connecting to GitHub…")

        tmp_dir  = Path(tempfile.mkdtemp(prefix="fetchly_upd_"))
        zip_path = tmp_dir / "fetchly_update.zip"

        try:
            req = urllib.request.Request(
                ZIP_URL,
                headers={"User-Agent": "Fetchly-Updater/1"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk = 65536
                self._progress(8, "Downloading update…")

                with open(zip_path, "wb") as f:
                    while True:
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        downloaded += len(buf)
                        if total:
                            pct = 8 + int((downloaded / total) * 52)  # 8 → 60
                            self._progress(pct, f"Downloading… {downloaded // 1024} KB")

        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False, f"Download failed: {e}"

        # ── Step 2: Extract ZIP ───────────────────────────────────────────
        self._progress(62, "Extracting update…")

        extract_dir = tmp_dir / "extracted"
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False, f"Extraction failed: {e}"

        # GitHub ZIPs contain a top-level folder like "Fetchly-master/"
        candidates = list(extract_dir.iterdir())
        if not candidates:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False, "Archive was empty."
        source_root = candidates[0]  # e.g. extract_dir/Fetchly-master/

        # ── Step 3: Copy files ────────────────────────────────────────────
        self._progress(70, "Installing files…")

        _copy_update(source_root, install_root)

        self._progress(95, "Cleaning up…")
        shutil.rmtree(tmp_dir, ignore_errors=True)

        self._progress(100, "Update complete! Restarting…")
        return True, "Update installed successfully."


# ─────────────────────────────────────────────
#  FILE COPY HELPER
# ─────────────────────────────────────────────

def _copy_update(source_root: Path, dest_root: Path) -> None:
    """
    Recursively copy files from source_root into dest_root,
    skipping entries listed in SKIP_PATHS.
    """
    for src_file in source_root.rglob("*"):
        if not src_file.is_file():
            continue

        rel_path = src_file.relative_to(source_root)
        rel_str  = str(rel_path).replace("\\", "/")

        # Skip protected paths
        if rel_str in SKIP_PATHS:
            continue
        # Skip hidden / system files
        if any(part.startswith(".") for part in rel_path.parts):
            continue
        # Skip __pycache__ and .pyc
        if "__pycache__" in rel_path.parts or src_file.suffix == ".pyc":
            continue

        dest_file = dest_root / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)


# ─────────────────────────────────────────────
#  APP RESTART
# ─────────────────────────────────────────────

def _restart_app() -> None:
    """Restart the app after an update."""
    # For PyInstaller frozen EXE, re-launch sys.executable (the EXE itself)
    # For dev (script) mode, re-launch python main.py
    try:
        if getattr(sys, 'frozen', False):
            # Running as compiled EXE — relaunch the EXE
            exe = sys.executable
            import subprocess
            subprocess.Popen([exe], creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0)
        else:
            main_py = str(Path(__file__).parent / "main.py")
            if sys.platform == "win32":
                import subprocess
                subprocess.Popen([sys.executable, main_py], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                os.execv(sys.executable, [sys.executable, main_py])
    except Exception:
        pass
    finally:
        os._exit(0)
