from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
import webview

from downloader_engine import (
    DownloadCancelled, DownloaderEngine, detect_platform,
    auto_update_ytdlp
)
from updater import FetchlyUpdater


# ─────────────────────────────────────────────
#  RESOURCE PATH (dev + PyInstaller EXE)
# ─────────────────────────────────────────────
def _resource_path(relative: str) -> str:
    """
    Resolve a path to a bundled asset.
    - Dev mode  : relative to this file's directory
    - PyInstaller: relative to sys._MEIPASS (the temp extraction folder)
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


# ─────────────────────────────────────────────
#  UTILITIES
# ─────────────────────────────────────────────
def safe_print(msg: str) -> None:
    try:
        print(msg)
    except Exception:
        try:
            print(str(msg).encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass


def read_windows_clipboard_text() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
    except Exception:
        return None

    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    cf_unicode_text = 13

    if not user32.OpenClipboard(None):
        return None

    try:
        handle = user32.GetClipboardData(cf_unicode_text)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def open_path(path: str) -> bool:
    try:
        if sys.platform == "win32":
            norm_path = os.path.normpath(path)
            try:
                subprocess.Popen(f'explorer /select,"{norm_path}"')
            except Exception:
                os.startfile(os.path.dirname(norm_path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        return True
    except Exception as exc:
        safe_print(f"[!] Open folder error: {exc}")
        return False


def safe_open_url(url: str) -> bool:
    try:
        return webbrowser.open(url, new=2)
    except Exception as exc:
        safe_print(f"[!] Browser open error: {exc}")
        return False


# ─────────────────────────────────────────────
#  CLIPBOARD WATCHER
# ─────────────────────────────────────────────
class ClipboardWatcher(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback    = callback
        self.enabled     = False
        self.last_clip   = ""
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        if sys.platform != "win32":
            return
        while not self._stop_event.is_set():
            if self.enabled:
                try:
                    clip = (read_windows_clipboard_text() or "").strip()
                    if clip and clip != self.last_clip:
                        self.last_clip = clip
                        if detect_platform(clip) or clip.lower().startswith("http"):
                            self.callback(clip)
                except Exception:
                    pass
            time.sleep(1.5)


# ─────────────────────────────────────────────
#  PYTHON ↔ WEBVIEW BRIDGE API
# ─────────────────────────────────────────────
class FetchlyAPI:
    def __init__(self):
        self.window         = None
        self.default_folder = os.path.join(os.path.expanduser("~"), "Downloads", "Fetchly")
        os.makedirs(self.default_folder, exist_ok=True)
        self.engine             = DownloaderEngine(progress_callback=self._progress_callback)
        self.current_info       = None
        self.download_thread    = None
        self.clipboard_watcher  = ClipboardWatcher(self._on_clipboard_detected)
        self.clipboard_watcher.start()

    # ── Folder ────────────────────────────────
    def get_default_folder(self) -> str:
        return self.default_folder

    # ── Platform detect ───────────────────────
    def detect_platform_js(self, url: str) -> str | None:
        return detect_platform(url)

    # ── Analyse URL ───────────────────────────
    def analyse(self, url: str) -> dict:
        try:
            info = self.engine.fetch_metadata(url)
            self.current_info = info
            return {
                "status": "ok",
                "info": {
                    "title":         info.title,
                    "author":        info.author,
                    "duration":      info.duration,
                    "views":         info.views,
                    "thumbnail_url": info.thumbnail_url,
                    "platform":      info.platform,
                    "url":           info.url,
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── Folder picker ─────────────────────────
    def select_folder(self) -> str | None:
        if self.window:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                return result[0]
        return None

    # ── Download ──────────────────────────────
    def download(self, quality: str, folder: str, formatOpt: str = "MP4 · H.264") -> dict:
        if not self.current_info:
            return {"status": "error", "message": "No media analysed yet."}
        if self.download_thread and self.download_thread.is_alive():
            return {"status": "error", "message": "Download already in progress."}

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(quality, folder, formatOpt),
            daemon=True,
        )
        self.download_thread.start()
        return {"status": "ok"}

    def _download_worker(self, quality: str, folder: str, formatOpt: str) -> None:
        try:
            result = self.engine.download(self.current_info, quality, folder, formatOpt)
            if self.window:
                self.window.evaluate_js(f"onDownloadComplete({json.dumps(result)})")
        except DownloadCancelled as e:
            if self.window:
                self.window.evaluate_js(f"onDownloadCancelled({json.dumps(str(e))})")
        except Exception as e:
            if self.window:
                self.window.evaluate_js(f"onDownloadError({json.dumps(str(e))})")
        finally:
            gc.collect()

    # ── Progress ──────────────────────────────
    def _progress_callback(self, pct: float, speed: str, detail: str) -> None:
        if self.window:
            self.window.evaluate_js(
                f"updateProgressBuffered({json.dumps(pct)}, {json.dumps(speed)}, {json.dumps(detail)})"
            )

    # ── Cancel ────────────────────────────────
    def cancel_download(self) -> dict:
        self.engine.request_cancel()
        return {"status": "ok"}

    # ── Search ────────────────────────────────
    def search_media(self, q: str, filter: str, limit: int) -> dict:
        try:
            results = self.engine.search_media(q, filter, limit)
            return {"status": "ok", "results": results}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── Preview stream ────────────────────────
    def preview_stream(self, url: str) -> dict:
        try:
            preview = self.engine.get_preview_stream(url)
            return {"status": "ok", "preview": preview}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def preview_stream_high_quality(self, url: str) -> dict:
        try:
            preview = self.engine.get_preview_stream(url, high_quality=True)
            return {"status": "ok", "preview": preview}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ── Open folder / URL ─────────────────────
    def open_folder(self, file_path: str) -> dict:
        open_path(file_path)
        return {"status": "ok"}

    def open_in_browser(self, url: str) -> dict:
        safe_open_url(url)
        return {"status": "ok"}

    def open_url(self, url: str) -> dict:
        """Alias for open_in_browser — called by the JS update-badge handler."""
        safe_open_url(url)
        return {"status": "ok"}

    # ── Clipboard ─────────────────────────────
    def set_clipboard_enabled(self, enabled: bool) -> dict:
        self.clipboard_watcher.enabled = enabled
        return {"status": "ok"}

    # ── Logging ───────────────────────────────
    def log_error(self, message: str) -> dict:
        safe_print(f"[Frontend Error] {message}")
        return {"status": "ok"}

    # ── Clipboard callback ────────────────────
    def _on_clipboard_detected(self, url: str) -> None:
        if self.window:
            self.window.evaluate_js(f"onClipboardDetected({json.dumps(url)})")

    # ── App / yt-dlp version info ─────────────
    def _resolve_installed_ytdlp_version(self) -> str | None:
        try:
            import yt_dlp.version
            ver = getattr(yt_dlp.version, "__version__", None)
            if ver:
                return str(ver)
        except Exception:
            pass
        try:
            import yt_dlp
            ver = getattr(yt_dlp, "__version__", None)
            if ver:
                return str(ver)
        except Exception:
            pass
        try:
            import importlib.metadata
            ver = importlib.metadata.version("yt-dlp")
            if ver:
                return str(ver)
        except Exception:
            pass
        return None

    def get_ytdlp_version(self) -> dict:
        """Returns the currently installed yt-dlp version string."""
        try:
            version = self._resolve_installed_ytdlp_version() or "unknown"
            return {"status": "ok", "version": version}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def check_app_updates(self) -> dict:
        """
        Checks PyPI for the latest yt-dlp release and compares to the
        installed version. Returns update info for the UI to display.
        Only signals update_available if installed version is recognized
        and latest version is strictly newer.
        """
        try:
            import requests as req
            import re

            current = self._resolve_installed_ytdlp_version()
            if not current or current.strip() in ("", "0", "unknown"):
                return {
                    "status": "ok",
                    "current_version": current or "unknown",
                    "latest_version": None,
                    "update_available": False,
                }

            resp = req.get(
                "https://pypi.org/pypi/yt-dlp/json",
                timeout=8,
                headers={"User-Agent": "Fetchly/1.0"},
            )
            resp.raise_for_status()
            latest = resp.json().get("info", {}).get("version", "")

            # Robust version comparison (YYYY.MM.DD or semantic versioning)
            def ver_tuple(v: str):
                if not v:
                    return (0,)
                parts = re.split(r"[.\-]", str(v).strip())
                nums = [int(x) for x in parts if x.isdigit()]
                return tuple(nums) if nums else (0,)

            curr_tup = ver_tuple(current)
            late_tup = ver_tuple(latest)

            update_available = bool(curr_tup > (0,) and late_tup > (0,) and late_tup > curr_tup)

            return {
                "status":           "ok",
                "current_version":  current,
                "latest_version":   latest,
                "update_available": update_available,
                "download_url":     "https://github.com/yt-dlp/yt-dlp/releases/latest",
            }
        except Exception as e:
            safe_print(f"[!] check_app_updates error: {e}")
            return {"status": "error", "message": str(e), "update_available": False}

    def update_ytdlp(self) -> dict:
        """
        Triggered from the UI — runs pip upgrade of yt-dlp in a background
        thread and notifies the frontend when done via onUpdateComplete(success).
        """
        def _worker():
            try:
                # Force a fresh upgrade attempt (bypass the 5-min cooldown)
                import yt_dlp as _ytdlp_mod
                import importlib
                import time as _time
                import downloader_engine as _de
                _de._ytdlp_last_updated = 0  # reset cooldown for manual trigger

                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                    capture_output=True, text=True, timeout=120
                )
                success = result.returncode == 0
                if success:
                    safe_print(f"[+] yt-dlp upgraded successfully.")
                    try:
                        importlib.reload(_ytdlp_mod)
                    except Exception as e:
                        safe_print(f"[!] hot-reload: {e}")
                    _de._ytdlp_last_updated = _time.time()
                else:
                    safe_print(f"[!] pip upgrade failed: {result.stderr[-300:]}")

                if self.window:
                    self.window.evaluate_js(
                        f"onUpdateComplete({json.dumps(success)})"
                    )
            except Exception as ex:
                safe_print(f"[!] update_ytdlp worker error: {ex}")
                if self.window:
                    self.window.evaluate_js("onUpdateComplete(false)")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return {"status": "ok", "message": "Upgrade started in background"}

    # ── Fetchly App Self-Update ────────────────
    def check_fetchly_update(self) -> dict:
        """
        Check GitHub for a new Fetchly version.
        Returns {status, current_version, latest_version, update_available, changelog}.
        Called from JS on startup — runs in a background thread so the UI never blocks.
        """
        try:
            updater = FetchlyUpdater()
            return updater.check()
        except Exception as ex:
            return {"status": "error", "error": str(ex),
                    "update_available": False, "current_version": "?",
                    "latest_version": "?", "changelog": ""}

    def install_fetchly_update(self) -> dict:
        """
        Download and apply the latest Fetchly update from GitHub.
        Progress is streamed to the UI via onFetchlyUpdateProgress(pct, msg).
        When done, calls onFetchlyUpdateDone(success) then restarts the app.
        """
        def _progress(pct: int, msg: str) -> None:
            if self.window:
                try:
                    self.window.evaluate_js(
                        f"onFetchlyUpdateProgress({pct}, {json.dumps(msg)})"
                    )
                except Exception:
                    pass

        def _done(success: bool, msg: str) -> None:
            safe_print(f"[Fetchly Updater] done={success} msg={msg}")
            if self.window:
                try:
                    self.window.evaluate_js(
                        f"onFetchlyUpdateDone({json.dumps(success)})"
                    )
                except Exception:
                    pass

        updater = FetchlyUpdater(progress_cb=_progress)
        updater.install(on_done=_done)
        return {"status": "ok", "message": "Update started in background"}




# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def _apply_window_icon():
    if sys.platform != "win32":
        return
    import ctypes
    for _ in range(25):
        time.sleep(0.25)
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "Fetchly")
            if hwnd:
                ico_path = _resource_path("fetchly.ico")
                if os.path.exists(ico_path):
                    IMAGE_ICON = 1
                    LR_LOADFROMFILE = 0x00000010
                    WM_SETICON = 0x0080
                    SMTO_ABORTIFHUNG = 0x0002
                    hicon_big = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                    hicon_small = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                    result = ctypes.c_ulonglong()
                    if hicon_big:
                        ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_SETICON, 1, hicon_big, SMTO_ABORTIFHUNG, 200, ctypes.byref(result))
                    if hicon_small:
                        ctypes.windll.user32.SendMessageTimeoutW(hwnd, WM_SETICON, 0, hicon_small, SMTO_ABORTIFHUNG, 200, ctypes.byref(result))
                    break
        except Exception:
            pass


def main():
    api       = FetchlyAPI()
    html_path = _resource_path("fetchly.html")
    url       = f"file:///{html_path.replace(os.sep, '/')}"

    window = webview.create_window(
        "Fetchly",
        url,
        js_api=api,
        width=1280,
        height=860,
        min_size=(1100, 760),
        background_color="#07070a",
    )
    api.window = window

    threading.Thread(target=_apply_window_icon, daemon=True).start()
    webview.start(debug=False)
