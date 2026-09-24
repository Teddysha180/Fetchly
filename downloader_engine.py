import io
import os
import re
import sys
import requests
import threading
import queue
import shutil
import base64
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from dataclasses import dataclass, field
import yt_dlp, yt_dlp.utils


# ─────────────────────────────────────────────
#  EXCEPTIONS
# ─────────────────────────────────────────────
class DownloadCancelled(Exception):
    pass


# ─────────────────────────────────────────────
#  DATA MODEL
# ─────────────────────────────────────────────
@dataclass
class MediaInfo:
    url:           str   = ""
    platform:      str   = ""
    title:         str   = "Untitled"
    author:        str   = "Unknown"
    duration:      str   = "--:--"
    views:         str   = "—"
    thumbnail_url: str   = ""
    raw_info:      dict  = field(default_factory=dict)


# ─────────────────────────────────────────────
#  QUALITY PRESETS
# ─────────────────────────────────────────────
QUALITY_PRESETS = {
    "Best":  "bestvideo[height<=2160][vcodec!=none]+bestaudio[acodec!=none]/bestvideo[height<=1080][vcodec!=none]+bestaudio[acodec!=none]/best",
    "4K":    "bestvideo[height<=2160][vcodec!=none]+bestaudio[acodec!=none]/bestvideo[height<=1080][vcodec!=none]+bestaudio[acodec!=none]/best",
    "1080p": "bestvideo[height<=1080][vcodec!=none]+bestaudio[acodec!=none]/best",
    "720p":  "bestvideo[height<=720][vcodec!=none]+bestaudio[acodec!=none]/best",
    "480p":  "bestvideo[height<=480][vcodec!=none]+bestaudio[acodec!=none]/best",
    "360p":  "bestvideo[height<=360][vcodec!=none]+bestaudio[acodec!=none]/best",
    "MP3":   "bestaudio[acodec!=none]/bestaudio/best",
}

# Error keywords that indicate an expired/outdated extractor needing yt-dlp upgrade
_YTDLP_EXPIRED_KEYWORDS = (
    "sign in", "bot", "403", "http error 403",
    "this video is unavailable", "video unavailable",
    "please sign in", "login required",
    "extractorerror", "unsupported url",
    "no video formats found", "got error",
    "unable to extract", "nsig extraction failed",
    "signature", "sabr", "player response",
)


# ─────────────────────────────────────────────
#  PLATFORM DETECTION
# ─────────────────────────────────────────────
def detect_platform(url: str) -> str | None:
    host = urlparse(url.lower()).netloc
    path = urlparse(url.lower()).path
    domains = {
        "youtube":   ["youtube.com", "youtu.be", "youtube.com/shorts"],
        "tiktok":    ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "vxtiktok.com"],
        "instagram": ["instagram.com"],
        "facebook":  ["facebook.com", "fb.watch", "fb.com"],
        "vimeo":     ["vimeo.com"],
        "pinterest": ["pinterest.", "pin.it"],
        "twitter":   ["twitter.com", "x.com"],
    }
    for key, dd in domains.items():
        if any(d in host or d in path for d in dd):
            return key
    return None


# ─────────────────────────────────────────────
#  FORMATTERS
# ─────────────────────────────────────────────
def fmt_duration(s) -> str:
    if not s: return "--:--"
    try:
        s = int(s); h, r = divmod(s, 3600); m, s = divmod(r, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    except: return "--:--"

def fmt_count(n) -> str:
    if n is None: return "—"
    try:
        if n >= 1e9:  return f"{n/1e9:.1f}B"
        if n >= 1e6:  return f"{n/1e6:.1f}M"
        if n >= 1e3:  return f"{n/1e3:.1f}K"
        return str(n)
    except: return "—"


# ─────────────────────────────────────────────
#  THUMBNAIL HELPER
# ─────────────────────────────────────────────
def get_base64_thumbnail(url: str) -> str:
    if not url:
        return ""
    try:
        if "ytimg.com" in url:
            url = url.replace("/hqdefault.", "/maxresdefault.").replace("/sddefault.", "/maxresdefault.")
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/png,image/jpeg,image/*;q=0.8"
        }
        r = requests.get(url, headers=hdrs, timeout=10)
        if r.status_code == 200:
            content_type = r.headers.get("content-type", "image/jpeg")
            encoded = base64.b64encode(r.content).decode("utf-8")
            return f"data:{content_type};base64,{encoded}"
    except:
        pass
    return url


def get_best_thumbnail(info_dict: dict) -> str:
    """Extracts the highest resolution thumbnail URL available."""
    thumbnails = info_dict.get("thumbnails")
    if not thumbnails:
        thumb = info_dict.get("thumbnail") or ""
        if "ytimg.com" in thumb:
            return thumb.replace("/hqdefault.", "/maxresdefault.")
        return thumb
    try:
        sorted_thumbs = sorted(thumbnails, key=lambda t: (int(t.get("width") or 0) * int(t.get("height") or 0)), reverse=True)
        return sorted_thumbs[0].get("url", "")
    except:
        return thumbnails[-1].get("url", "") if thumbnails else ""


# ─────────────────────────────────────────────
#  FFMPEG / NODE HELPERS
# ─────────────────────────────────────────────
def find_ffmpeg() -> str | None:
    for exe in ("ffmpeg", "ffmpeg.exe"):
        path = shutil.which(exe)
        if path:
            return path
    try:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        for exe in ("ffmpeg", "ffmpeg.exe"):
            path = os.path.join(base_dir, exe)
            if os.path.isfile(path) and (sys.platform == 'win32' or os.access(path, os.X_OK)):
                return path
    except:
        pass
    try:
        for folder in (".fetchly", ".mediavault"):
            home_bin = os.path.join(os.path.expanduser("~"), folder, "bin")
            for exe in ("ffmpeg", "ffmpeg.exe"):
                path = os.path.join(home_bin, exe)
                if os.path.isfile(path) and (sys.platform == 'win32' or os.access(path, os.X_OK)):
                    return path
    except:
        pass
    return None


def find_node() -> str | None:
    path = shutil.which("node") or shutil.which("node.exe")
    if path:
        return path
    for p in [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\node\node.exe"),
        os.path.expandvars(r"%APPDATA%\npm\node.cmd"),
    ]:
        if os.path.isfile(p):
            return p
    return None


# ─────────────────────────────────────────────
#  YT-DLP AUTO-UPDATE
# ─────────────────────────────────────────────
_ytdlp_update_lock = threading.Lock()
_ytdlp_last_updated: float = 0.0   # epoch seconds


def auto_update_ytdlp(progress_callback=None) -> bool:
    """
    Upgrades yt-dlp via pip.  Thread-safe and rate-limited to once per session
    (at most once every 5 minutes).  Returns True if upgrade ran successfully.
    """
    import time
    global _ytdlp_last_updated

    with _ytdlp_update_lock:
        now = time.time()
        if now - _ytdlp_last_updated < 300:   # 5-minute cool-down
            return False

        if getattr(sys, 'frozen', False):
            print("[*] Running inside PyInstaller bundle; in-process pip upgrade disabled.")
            return False

        if progress_callback:
            progress_callback(0, "—", "yt-dlp API expired — upgrading yt-dlp…")

        print("[*] yt-dlp extractor error detected — attempting automatic upgrade…")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print("[+] yt-dlp upgraded successfully.")
                print(result.stdout[-500:] if result.stdout else "")
                # Reload yt-dlp in-process so the new version takes effect
                import importlib
                try:
                    importlib.reload(yt_dlp)
                    importlib.reload(yt_dlp.utils)
                except Exception as reload_err:
                    print(f"[!] Could not hot-reload yt-dlp ({reload_err}). Restart may be needed.")
                _ytdlp_last_updated = time.time()
                if progress_callback:
                    progress_callback(0, "—", "yt-dlp upgraded — retrying download…")
                return True
            else:
                print(f"[!] pip upgrade failed (rc={result.returncode}): {result.stderr[-300:]}")
        except Exception as e:
            print(f"[!] auto_update_ytdlp exception: {e}")
        return False


def _is_expired_error(exc: Exception) -> bool:
    """Returns True if the exception looks like an expired/outdated extractor."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _YTDLP_EXPIRED_KEYWORDS)


# ─────────────────────────────────────────────
#  COOKIES-FROM-BROWSER
# ─────────────────────────────────────────────
def _get_cookie_opts() -> dict:
    """
    Try to get cookies from an installed browser.
    Returns yt-dlp options dict with cookiesfrombrowser if a browser is found.
    """
    for browser in ("chrome", "firefox", "edge", "brave", "chromium", "opera"):
        try:
            # Quick test: can yt-dlp load cookies from this browser?
            test_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "cookiesfrombrowser": (browser,),
            }
            # We just return the opts without testing — yt-dlp will silently skip
            # if the browser profile doesn't exist
            return {"cookiesfrombrowser": (browser,)}
        except Exception:
            continue
    return {}


# ─────────────────────────────────────────────
#  YT-DLP COMMON OPTIONS
# ─────────────────────────────────────────────
def get_ytdlp_common_opts() -> dict:
    """Returns baseline yt-dlp options shared across all calls."""
    opts = {
        "quiet": True,
        "no_warnings": True,
    }
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
    node_path = find_node()
    if node_path:
        opts["js_runtimes"] = {"node": {"path": node_path}}
    else:
        opts["js_runtimes"] = {"node": {}}
    return opts


def _get_platform_extra_opts(platform: str) -> dict:
    """Per-platform extra yt-dlp options (cookies, headers, etc.)."""
    extra: dict = {}
    if platform in ("instagram", "facebook", "twitter"):
        extra.update(_get_cookie_opts())
    return extra


# ─────────────────────────────────────────────
#  LOGGER
# ─────────────────────────────────────────────
class YTDLPLogger:
    def debug(self, msg):
        msg_str = str(msg)
        if "[Merger]" in msg_str or "Merging formats" in msg_str:
            print("[*] Merging video and audio streams using FFmpeg…")
        elif "ExtractAudio" in msg_str:
            print("[*] Extracting audio stream…")

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        print(f"[!] yt-dlp error: {msg}")


# ─────────────────────────────────────────────
#  MAIN ENGINE
# ─────────────────────────────────────────────
class DownloaderEngine:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self._cancel_event = threading.Event()
        self.last_update_time = 0
        self.last_printed_pct = -10

    # ── Cancel helpers ────────────────────────
    def request_cancel(self):
        self._cancel_event.set()

    def reset_cancel(self):
        self._cancel_event.clear()

    def is_cancel_requested(self):
        return self._cancel_event.is_set()

    def _ensure_not_cancelled(self):
        if self.is_cancel_requested():
            raise DownloadCancelled("Download cancelled")

    # ── FFmpeg auto-installer ─────────────────
    def ensure_ffmpeg_installed(self):
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            return

        self._ensure_not_cancelled()

        if self.progress_callback:
            self.progress_callback(0, "—", "FFmpeg missing. Starting download…")

        import platform
        import zipfile

        os_name = platform.system().lower()
        is_64bit = sys.maxsize > 2**32

        if os_name == "windows":
            arch = "win-64" if is_64bit else "win-32"
        elif os_name == "darwin":
            arch = "osx-64"
        elif os_name == "linux":
            arch = "linux-64" if is_64bit else "linux-32"
        else:
            print("[!] Unsupported platform for auto-downloading FFmpeg.")
            return

        ffmpeg_url  = f"https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v4.4.1/ffmpeg-4.4.1-{arch}.zip"
        ffprobe_url = f"https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v4.4.1/ffprobe-4.4.1-{arch}.zip"

        home_bin = os.path.join(os.path.expanduser("~"), ".fetchly", "bin")
        os.makedirs(home_bin, exist_ok=True)

        def download_and_extract(url, name):
            self._ensure_not_cancelled()
            zip_path = os.path.join(home_bin, f"{name}.zip")
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, stream=True, timeout=60)
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=16384):
                    self._ensure_not_cancelled()
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size and self.progress_callback:
                            pct = (downloaded / total_size) * 100
                            overall_pct = pct * 0.6 if name == "ffmpeg" else 60.0 + (pct * 0.4)
                            self.progress_callback(
                                overall_pct,
                                f"{downloaded/1048576:.1f}MB/s",
                                f"Downloading dependency: {name} ({downloaded/1048576:.1f}/{total_size/1048576:.1f} MB)"
                            )
            self._ensure_not_cancelled()
            if self.progress_callback:
                self.progress_callback(60.0 if name == "ffmpeg" else 100.0, "—", f"Extracting {name}…")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(home_bin)
            try:
                os.remove(zip_path)
            except:
                pass
            if os_name != "windows":
                for exe in (name, f"{name}.exe"):
                    p = os.path.join(home_bin, exe)
                    if os.path.exists(p):
                        try:
                            os.chmod(p, 0o755)
                        except:
                            pass

        try:
            download_and_extract(ffmpeg_url, "ffmpeg")
            download_and_extract(ffprobe_url, "ffprobe")
            if self.progress_callback:
                self.progress_callback(100.0, "—", "FFmpeg dependencies installed successfully!")
        except Exception as e:
            print(f"[!] Error downloading FFmpeg: {e}")
            if self.progress_callback:
                self.progress_callback(0, "—", f"Dependency installation failed: {e}")
            raise RuntimeError(f"Could not auto-download FFmpeg. Please install FFmpeg manually. Error: {e}") from e

    # ── Preview helpers ───────────────────────
    def _build_high_quality_preview_file(self, raw: dict, url: str, platform: str) -> str | None:
        preview_root = os.path.join(tempfile.gettempdir(), "fetchly_preview")
        os.makedirs(preview_root, exist_ok=True)
        preview_dir = tempfile.mkdtemp(prefix=f"{platform}_", dir=preview_root)
        info = MediaInfo(
            url=url, platform=platform,
            title=raw.get("title") or "Untitled",
            author=raw.get("uploader") or raw.get("channel") or "Unknown",
            duration=fmt_duration(raw.get("duration")),
            views=fmt_count(raw.get("view_count")),
            thumbnail_url=get_best_thumbnail(raw),
            raw_info=raw,
        )
        try:
            result = self._dl_ytdlp(info, "Best", preview_dir, "MP4 · H.264")
        except Exception as e:
            print(f"[!] High-quality preview fallback failed: {e}")
            return None
        file_path = result.get("file") if isinstance(result, dict) else None
        if not file_path or not os.path.exists(file_path):
            return None
        return file_path

    def get_preview_stream(self, url: str, high_quality: bool = False) -> dict:
        platform = detect_platform(url)
        if not platform:
            raise ValueError("Unsupported platform")

        opts = {
            **get_ytdlp_common_opts(),
            **_get_platform_extra_opts(platform),
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
        }

        raw = None
        last_err = None
        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    raw = ydl.extract_info(url, download=False)
                break
            except yt_dlp.utils.DownloadError as e:
                last_err = e
                if attempt == 0 and _is_expired_error(e):
                    upgraded = auto_update_ytdlp(self.progress_callback)
                    if not upgraded:
                        break
                else:
                    break
            except Exception as e:
                last_err = e
                break

        if raw is None:
            raise ValueError(f"Failed to fetch preview stream: {last_err}")

        local_preview = None
        if platform == "youtube" and high_quality:
            local_preview = self._build_high_quality_preview_file(raw, url, platform)

        preview_url, mime_type = self._pick_preview_format(raw)
        if local_preview:
            preview_url = Path(local_preview).resolve().as_uri()
            mime_type = "video/mp4"

        if not preview_url:
            raise ValueError("No playable preview stream was found")

        streams = []
        if not local_preview:
            formats = raw.get("formats") or []
            for fmt in formats:
                if not fmt or not fmt.get("url"):
                    continue
                if fmt.get("vcodec") in (None, "none") or fmt.get("acodec") in (None, "none"):
                    continue
                protocol = (fmt.get("protocol") or "").lower()
                if protocol and not any(p in protocol for p in ("https", "http", "m3u8")):
                    continue
                height = fmt.get("height") or 0
                if height == 0:
                    continue
                streams.append({
                    "url": fmt.get("url"),
                    "mime_type": fmt.get("mime_type") or "video/mp4",
                    "height": height,
                    "label": f"{height}p",
                })
            streams.sort(key=lambda s: s["height"], reverse=True)
            seen = set()
            unique_streams = []
            for s in streams:
                if s["height"] not in seen:
                    seen.add(s["height"])
                    unique_streams.append(s)
            streams = unique_streams
            if not streams and preview_url:
                height = raw.get("height") or 0
                label = f"{height}p" if height else "Auto"
                streams.append({
                    "url": preview_url,
                    "mime_type": mime_type or "video/mp4",
                    "height": height,
                    "label": label,
                })

        return {
            "stream_url": preview_url,
            "mime_type": mime_type,
            "title": raw.get("title") or "Untitled",
            "thumbnail_url": get_best_thumbnail(raw),
            "platform": platform,
            "streams": streams,
        }

    # ── Search ────────────────────────────────
    def search_media(self, query: str, media_filter: str = "all", limit: int = 40) -> list[dict]:
        query = (query or "").strip()
        if not query:
            raise ValueError("Search query is empty")

        search_query = self._build_search_query(query, media_filter, limit)
        opts = {
            **get_ytdlp_common_opts(),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "default_search": "ytsearch",
            "extract_flat": "in_playlist",
            "lazy_playlist": True,
            "socket_timeout": 10,
        }

        raw = None
        last_err = None
        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    raw = ydl.extract_info(search_query, download=False)
                break
            except yt_dlp.utils.DownloadError as e:
                last_err = e
                if attempt == 0 and _is_expired_error(e):
                    upgraded = auto_update_ytdlp(self.progress_callback)
                    if not upgraded:
                        break
                else:
                    break
            except Exception as e:
                last_err = e
                break

        if raw is None:
            raise ValueError(f"Search failed: {last_err}")

        entries = raw.get("entries") or []
        results = []
        for entry in entries:
            if not entry:
                continue
            result_type = self._classify_search_result(entry)
            if media_filter == "video" and result_type != "video":
                continue
            if media_filter == "music" and result_type != "music":
                continue
            if media_filter == "short" and result_type != "short":
                continue
            if media_filter == "live" and result_type != "live":
                continue

            video_url     = self._normalize_search_url(entry)
            thumbnail_url = self._search_thumbnail(entry)
            video_id      = entry.get("id") or entry.get("url") or ""

            results.append({
                "id":          video_id,
                "title":       entry.get("title") or "Untitled",
                "channel":     entry.get("uploader") or entry.get("channel") or "Unknown Channel",
                "platform":    "youtube",
                "type":        result_type,
                "duration":    None if result_type == "live" else fmt_duration(entry.get("duration")),
                "views":       self._format_search_views(entry, result_type),
                "description": self._summarize_description(entry.get("description")),
                "thumbnail_url": thumbnail_url,
                "url":         video_url,
            })

            if len(results) >= limit:
                break

        print(f"[*] Search result JSON output count: {len(results)}")
        return results

    def _build_search_query(self, query: str, media_filter: str, limit: int) -> str:
        suffix = {
            "all":   "",
            "video": " video",
            "music": " music audio song",
            "short": " shorts",
            "live":  " live",
        }.get((media_filter or "all").lower(), "")
        overfetch = 1.15 if media_filter in {"all", "video"} else 1.6
        return f"ytsearch{int(limit * overfetch)}:{query}{suffix}"

    def _classify_search_result(self, entry: dict) -> str:
        url        = (entry.get("webpage_url") or entry.get("url") or "").lower()
        title      = (entry.get("title") or "").lower()
        duration   = entry.get("duration")
        live_status = (entry.get("live_status") or "").lower()
        if live_status in {"is_live", "post_live", "was_live"}:
            return "live"
        if "/shorts/" in url or "#shorts" in title or (duration and duration <= 60):
            return "short"
        if entry.get("track") or entry.get("album") or "official audio" in title or "lyrics" in title:
            return "music"
        return "video"

    def _format_search_views(self, entry: dict, result_type: str) -> str:
        count = fmt_count(entry.get("view_count"))
        if count == "—":
            return count
        return f"{count} plays" if result_type == "music" else f"{count} views"

    def _normalize_search_url(self, entry: dict) -> str:
        video_id = entry.get("id") or entry.get("url") or ""
        if video_id and not video_id.startswith("http"):
            if "v=" in video_id:
                m = re.search(r'v=([A-Za-z0-9_-]{11})', video_id)
                if m:
                    video_id = m.group(1)
            elif "/" in video_id:
                video_id = video_id.split("/")[-1]
            return f"https://www.youtube.com/watch?v={video_id}"
        webpage_url = entry.get("webpage_url") or ""
        if webpage_url.startswith("http://") or webpage_url.startswith("https://"):
            return webpage_url
        entry_url = entry.get("url") or ""
        if entry_url.startswith("http://") or entry_url.startswith("https://"):
            return entry_url
        return ""

    def _search_thumbnail(self, entry: dict) -> str:
        best = get_best_thumbnail(entry)
        if best:
            return best
        video_id = entry.get("id")
        if video_id:
            return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
        return ""

    def _summarize_description(self, description: str | None) -> str:
        if not description:
            return ""
        text = " ".join(str(description).split())
        return text[:140].rstrip() + ("..." if len(text) > 140 else "")

    def _pick_preview_format(self, raw: dict) -> tuple[str | None, str | None]:
        formats = raw.get("formats") or []
        progressive = []
        for fmt in formats:
            if not fmt or not fmt.get("url"):
                continue
            if fmt.get("vcodec") in (None, "none") or fmt.get("acodec") in (None, "none"):
                continue
            if fmt.get("ext") != "mp4":
                continue
            protocol = (fmt.get("protocol") or "").lower()
            if protocol and not any(p in protocol for p in ("https", "http", "m3u8")):
                continue
            progressive.append(fmt)

        if progressive:
            progressive.sort(key=lambda f: f.get("height") or 0)
            best = progressive[0]
            for f in progressive:
                if (f.get("height") or 0) >= 360:
                    best = f
                    break
            return best.get("url"), best.get("mime_type") or "video/mp4"

        direct_url = raw.get("url")
        if direct_url:
            ext  = raw.get("ext") or "mp4"
            mime = "application/x-mpegURL" if ext == "m3u8" else f"video/{ext}"
            return direct_url, mime

        return None, None

    # ── Metadata fetch ────────────────────────
    def fetch_metadata(self, url: str) -> MediaInfo:
        platform = detect_platform(url)
        if not platform:
            raise ValueError("Unsupported platform")

        info = MediaInfo(url=url, platform=platform)

        if platform == "tiktok":
            info = self._fetch_tiktok_metadata(url, info)
        else:
            opts = {
                **get_ytdlp_common_opts(),
                **_get_platform_extra_opts(platform),
                "skip_download": True,
                "no_warnings": True,
            }
            raw = None
            last_err = None
            for attempt in range(2):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        raw = ydl.extract_info(url, download=False)
                    break
                except yt_dlp.utils.DownloadError as e:
                    last_err = e
                    if attempt == 0 and _is_expired_error(e):
                        upgraded = auto_update_ytdlp(self.progress_callback)
                        if not upgraded:
                            raise ValueError(f"Failed to fetch metadata from {platform}: {e}") from e
                    else:
                        raise ValueError(f"Failed to fetch metadata from {platform}: {e}") from e
                except Exception as e:
                    raise RuntimeError(f"Unexpected error with yt-dlp for {platform}: {e}") from e

            if raw is None:
                raise ValueError(f"Failed to fetch metadata from {platform}: {last_err}")

            info.title        = raw.get("title") or "Untitled"
            info.author       = raw.get("uploader") or raw.get("channel") or "Unknown"
            info.duration     = fmt_duration(raw.get("duration"))
            info.views        = fmt_count(raw.get("view_count"))
            info.thumbnail_url = get_best_thumbnail(raw)
            info.raw_info     = raw

        if platform != "youtube" and info.thumbnail_url:
            info.thumbnail_url = get_base64_thumbnail(info.thumbnail_url)

        return info

    def _fetch_tiktok_metadata(self, url: str, info: MediaInfo) -> MediaInfo:
        """
        Multi-fallback TikTok metadata:
        1. tikwm.com API
        2. yt-dlp fallback
        """
        # Attempt 1: tikwm.com
        try:
            r = requests.get(
                f"https://www.tikwm.com/api/?url={url}&hd=1",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30
            )
            r.raise_for_status()
            p = r.json()
            if p.get("code") == 0:
                d = p["data"]
                info.title    = d.get("title") or f"TikTok @{d.get('author', {}).get('nickname', '')}"
                info.author   = d.get("author", {}).get("nickname", "Unknown")
                info.duration = fmt_duration(d.get("duration"))
                info.views    = fmt_count(d.get("play_count"))
                info.thumbnail_url = d.get("origin_cover") or d.get("cover") or ""
                info.raw_info = d
                print("[*] TikTok metadata via tikwm.com")
                return info
            print(f"[!] tikwm.com returned code {p.get('code')}: {p.get('msg')}")
        except Exception as e:
            print(f"[!] tikwm.com failed: {e}")

        # Attempt 2: yt-dlp fallback
        try:
            opts = {
                **get_ytdlp_common_opts(),
                "skip_download": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=False)
            info.title        = raw.get("title") or "TikTok Video"
            info.author       = raw.get("uploader") or raw.get("channel") or "Unknown"
            info.duration     = fmt_duration(raw.get("duration"))
            info.views        = fmt_count(raw.get("view_count"))
            info.thumbnail_url = get_best_thumbnail(raw)
            info.raw_info     = raw
            print("[*] TikTok metadata via yt-dlp fallback")
            return info
        except Exception as e:
            raise RuntimeError(f"Failed to fetch TikTok metadata: {e}") from e

    # ── Download entry point ──────────────────
    def download(self, info: MediaInfo, quality: str, folder: str, format_opt: str = "MP4 · H.264"):
        self.reset_cancel()
        os.makedirs(folder, exist_ok=True)
        try:
            plat = info.platform
            if plat == "tiktok":
                return self._dl_tiktok(info, folder)
            if plat == "pinterest":
                return self._dl_pinterest(info, folder)
            # Ensure ffmpeg/ffprobe are available for all other platforms
            self.ensure_ffmpeg_installed()
            return self._dl_ytdlp(info, quality, folder, format_opt)
        finally:
            self.reset_cancel()

    # ── File validation ───────────────────────
    def _validate_file(self, file_path, ffmpeg_path):
        if not ffmpeg_path:
            print("[!] FFmpeg path not available. Skipping validation of downloaded file.")
            return
        ffprobe_dir = os.path.dirname(ffmpeg_path)
        ffprobe_exe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
        ffprobe_path = os.path.join(ffprobe_dir, ffprobe_exe)
        if not os.path.exists(ffprobe_path):
            ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path or not os.path.exists(ffprobe_path):
            print("[!] ffprobe not found. Skipping validation.")
            return
        print(f"\n[*] Validating downloaded file: {file_path}")
        try:
            cmd = [
                ffprobe_path, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,bitrate,r_frame_rate,codec_name",
                "-of", "json", file_path
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0:
                data    = json.loads(res.stdout)
                streams = data.get("streams", [])
                if streams:
                    vs = streams[0]
                    print(f"[+] Final resolution: {vs.get('width')}x{vs.get('height')} ({vs.get('height')}p)")
                    print(f"[+] Final codec: {vs.get('codec_name')}")
                    print(f"[+] Final FPS: {vs.get('r_frame_rate')}")
                    br = vs.get("bitrate")
                    if br:
                        print(f"[+] Final bitrate: {int(br)/1000:.1f} kbps")
                else:
                    print("[!] No video streams found in downloaded file.")
            else:
                print(f"[!] ffprobe failed: {res.stderr}")
        except Exception as e:
            print(f"[!] Validation exception: {e}")

    # ── yt-dlp download (with auto-retry) ─────
    def _dl_ytdlp(self, info: MediaInfo, quality: str, folder: str, format_opt: str = "MP4 · H.264") -> dict:
        self._ensure_not_cancelled()

        for attempt in range(2):
            try:
                return self._dl_ytdlp_inner(info, quality, folder, format_opt)
            except yt_dlp.utils.DownloadError as e:
                if self.is_cancel_requested():
                    raise DownloadCancelled("Download cancelled") from e
                if attempt == 0 and _is_expired_error(e):
                    print(f"[!] Download error (attempt {attempt+1}): {e}")
                    upgraded = auto_update_ytdlp(self.progress_callback)
                    if upgraded:
                        print("[*] Retrying download after yt-dlp upgrade…")
                        continue
                raise
        # Should not reach here
        raise RuntimeError("Download failed after upgrade retry")

    def _dl_ytdlp_inner(self, info: MediaInfo, quality: str, folder: str, format_opt: str) -> dict:
        """Core yt-dlp download logic (single attempt)."""
        self._ensure_not_cancelled()
        ffmpeg_path = find_ffmpeg()

        # Freshly extract formats
        fetch_opts = {
            **get_ytdlp_common_opts(),
            **_get_platform_extra_opts(info.platform),
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        formats = []
        try:
            with yt_dlp.YoutubeDL(fetch_opts) as ydl:
                raw_info = ydl.extract_info(info.url, download=False)
                formats = raw_info.get("formats", [])
        except Exception as e:
            print(f"[!] Error fetching formats for selection: {e}")
            if info.raw_info and "formats" in info.raw_info:
                formats = info.raw_info["formats"]

        # Print available formats
        print("\n=== AVAILABLE FORMATS ===")
        for f in formats:
            fid = f.get("format_id", "?")
            w   = f.get("width")
            h   = f.get("height")
            fps = f.get("fps")
            vc  = f.get("vcodec", "none")
            ac  = f.get("acodec", "none")
            br  = f.get("tbr") or f.get("bitrate") or 0
            ext = f.get("ext", "?")
            print(f"Format ID: {fid} | Res: {w}x{h} ({h}p) | FPS: {fps} | VCodec: {vc} | ACodec: {ac} | Bitrate: {br} kbps | Ext: {ext}")
        print("==========================\n")

        fmt_lower  = format_opt.lower() if format_opt else ""
        is_audio   = "mp3" in fmt_lower or "aac" in fmt_lower or "flac" in fmt_lower

        req_q = (quality or "Best").strip()
        if req_q == "4K":
            target_h_pref = 2160
            max_height = 4320
        elif req_q == "1080p":
            target_h_pref = 1080
            max_height = 1080
        elif req_q == "720p":
            target_h_pref = 720
            max_height = 720
        elif req_q == "480p":
            target_h_pref = 480
            max_height = 480
        elif req_q == "360p":
            target_h_pref = 360
            max_height = 360
        else:  # "Best"
            target_h_pref = None
            max_height = 99999

        selected_format_str = ""
        reason = ""

        if is_audio:
            audio_streams = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
            if not audio_streams:
                audio_streams = [f for f in formats if f.get("acodec") not in (None, "none")]
            if audio_streams:
                best_audio    = max(audio_streams, key=lambda f: f.get("tbr") or f.get("abr") or f.get("bitrate") or 0)
                best_audio_id = best_audio.get("format_id")
                selected_format_str = f"{best_audio_id}/bestaudio/best"
                reason = f"Selected highest quality audio stream {best_audio_id}"
            else:
                selected_format_str = "bestaudio/best"
                reason = "No explicit audio stream found, using bestaudio/best"
        else:
            video_streams    = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height") is not None]
            candidate_video  = [f for f in video_streams if f.get("height") <= max_height]
            if not candidate_video:
                candidate_video = video_streams

            if candidate_video:
                available_heights = sorted(set(f.get("height") for f in candidate_video if f.get("height")), reverse=True)
                target_height = None
                if target_h_pref and target_h_pref in available_heights:
                    target_height = target_h_pref
                elif target_h_pref:
                    under_pref = [h for h in available_heights if h <= target_h_pref]
                    target_height = under_pref[0] if under_pref else available_heights[0]
                else:
                    # "Best" -> pick the maximum resolution available (up to 4K / 8K)
                    target_height = available_heights[0]

                target_videos = [f for f in candidate_video if f.get("height") == target_height]

                def get_sorting_score(f):
                    pref_score = 0
                    vc  = (f.get("vcodec") or "").lower()
                    ext = (f.get("ext") or "").lower()
                    if "h.264" in fmt_lower or "mp4" in fmt_lower:
                        if "avc" in vc or "h264" in vc: pref_score += 2
                        if ext == "mp4":                 pref_score += 1
                    elif "h.265" in fmt_lower:
                        if "hevc" in vc or "h265" in vc: pref_score += 2
                        if ext == "mp4":                  pref_score += 1
                    elif "vp9" in fmt_lower:
                        if "vp9" in vc:   pref_score += 2
                        if ext == "webm": pref_score += 1
                    br  = f.get("tbr") or f.get("bitrate") or 0
                    fps = f.get("fps") or 0
                    return (pref_score, br, fps)

                best_video    = max(target_videos, key=get_sorting_score)
                best_video_id = best_video.get("format_id")

                if ffmpeg_path:
                    audio_streams = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
                    if not audio_streams:
                        audio_streams = [f for f in formats if f.get("acodec") not in (None, "none")]
                    if audio_streams:
                        best_audio    = max(audio_streams, key=lambda f: f.get("tbr") or f.get("abr") or f.get("bitrate") or 0)
                        best_audio_id = best_audio.get("format_id")
                        selected_format_str = f"{best_video_id}+{best_audio_id}/bestvideo[height<={max_height}]+bestaudio/bestvideo+bestaudio/best"
                        reason = f"Download {best_video.get('height')}p (ID:{best_video_id}) + audio (ID:{best_audio_id})"
                    else:
                        selected_format_str = f"{best_video_id}/bestvideo[height<={max_height}]+bestaudio/best"
                        reason = f"Selected video {best_video.get('height')}p (ID:{best_video_id})"
                else:
                    progressive_streams = [f for f in candidate_video if f.get("acodec") not in (None, "none")]
                    if progressive_streams:
                        avail_prog = sorted(set(f.get("height") for f in progressive_streams if f.get("height")), reverse=True)
                        p_target = avail_prog[0]
                        best_prog = max([f for f in progressive_streams if f.get("height") == p_target], key=get_sorting_score)
                        selected_format_str = f"{best_prog.get('format_id')}/best[height<={max_height}]/best"
                        reason = f"No FFmpeg. Progressive {p_target}p (ID:{best_prog.get('format_id')})"
                    else:
                        selected_format_str = f"best[height<={max_height}]/best"
                        reason = "No FFmpeg and no progressive formats — using default best"
            else:
                selected_format_str = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
                reason = "No candidate video formats — falling back to yt-dlp default"

        print(f"[*] Quality requested: {quality}")
        print(f"[*] Format Option: {format_opt}")
        print(f"[*] Selected format: {selected_format_str}")
        print(f"[*] Reason: {reason}\n")

        self.last_update_time  = 0
        self.last_printed_pct  = -10
        print("[*] Download started…")

        browser_headers = {
            "User-Agent":              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "Accept-Language":         "en-US,en;q=0.9",
            "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Mode":          "navigate",
            "Sec-Fetch-Site":          "none",
            "Sec-Fetch-User":          "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        opts = {
            **get_ytdlp_common_opts(),
            **_get_platform_extra_opts(info.platform),
            "outtmpl":          os.path.join(folder, "%(title)s.%(ext)s"),
            "progress_hooks":   [self._ytdlp_hook],
            "quiet":            True,
            "no_warnings":      True,
            "no_color":         True,
            "continuedl":       True,
            "socket_timeout":   60,
            "retries":          10,
            "fragment_retries": 10,
            "format":           selected_format_str,
            "http_headers":     browser_headers,
            "logger":           YTDLPLogger(),
        }

        if ffmpeg_path:
            opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)

        if is_audio:
            audio_codec   = None
            audio_quality = None
            if "mp3"  in fmt_lower: audio_codec, audio_quality = "mp3",  "320"
            elif "aac"  in fmt_lower: audio_codec, audio_quality = "m4a",  "256"
            elif "flac" in fmt_lower: audio_codec, audio_quality = "flac", "0"
            if audio_codec:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec":    audio_codec,
                    "preferredquality":  audio_quality,
                }]
        else:
            opts["merge_output_format"] = "mkv" if "mkv" in fmt_lower else "mp4"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([info.url])
        except yt_dlp.utils.DownloadError as e:
            if self.is_cancel_requested():
                raise DownloadCancelled("Download cancelled") from e
            print(f"[!] Primary format download failed ({e}). Retrying with resilient fallback…")
            fallback_opts = dict(opts)
            fallback_opts["format"] = "bestaudio/best" if is_audio else f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best"
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl_fb:
                    ydl_fb.download([info.url])
            except Exception as fb_err:
                if self.is_cancel_requested():
                    raise DownloadCancelled("Download cancelled") from fb_err
                raise fb_err from e

        f = self._get_latest_file(folder)
        if f and not is_audio:
            self._validate_file(f, ffmpeg_path)
        return {"file": f, "size": os.path.getsize(f) if f else 0}

    # ── Progress hook ─────────────────────────
    def _ytdlp_hook(self, d):
        import time
        self._ensure_not_cancelled()
        if d["status"] == "downloading":
            pct = 0.0
            raw_pct = d.get("_percent_str")
            if raw_pct:
                cleaned_pct = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(raw_pct)).replace("%", "").strip()
                try:
                    pct = float(cleaned_pct)
                except Exception:
                    pct = 0.0
            if pct <= 0.0:
                dl_bytes = d.get("downloaded_bytes") or 0
                tot_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if dl_bytes and tot_bytes:
                    pct = (dl_bytes / tot_bytes) * 100

            # Clean speed calculation (avoids terminal escape codes)
            spd_val = d.get("speed")
            if spd_val and isinstance(spd_val, (int, float)) and spd_val > 0:
                if spd_val >= 1048576:
                    spd = f"{spd_val / 1048576:.2f} MB/s"
                else:
                    spd = f"{spd_val / 1024:.0f} KB/s"
            else:
                raw_spd = d.get("_speed_str") or "—"
                spd = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(raw_spd)).strip() or "—"

            # Clean downloaded / total bytes (avoids terminal escape codes)
            dl_b = d.get("downloaded_bytes") or 0
            tot_b = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if dl_b and tot_b:
                detail = f"{dl_b / 1048576:.1f} MB / {tot_b / 1048576:.1f} MB"
            elif dl_b:
                detail = f"{dl_b / 1048576:.1f} MB downloaded"
            else:
                raw_dl = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(d.get("_downloaded_bytes_str", ""))).strip()
                raw_tot = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', str(d.get("_total_bytes_str", ""))).strip()
                detail = f"{raw_dl} / {raw_tot}" if raw_dl and raw_tot else "Downloading…"

            pct_int = int(pct)
            if pct_int >= self.last_printed_pct + 10 or pct_int >= 100:
                self.last_printed_pct = (pct_int // 10) * 10
                print(f"[*] Download progress: {pct_int}% (Speed: {spd})")

            now = time.time()
            if now - self.last_update_time >= 0.3 or pct >= 100:
                self.last_update_time = now
                if self.progress_callback:
                    self.progress_callback(pct, spd, detail)

        elif d["status"] == "finished":
            print("[*] Download complete. Finalising streams…")
            if self.progress_callback:
                self.progress_callback(100, "—", "Finalising streams…")

    # ── TikTok download (multi-fallback) ──────
    def _dl_tiktok(self, info: MediaInfo, folder: str) -> dict:
        self._ensure_not_cancelled()
        d = info.raw_info

        # Check if raw_info came from tikwm (has 'id', 'hdplay', etc.)
        # or from yt-dlp (has 'formats')
        if d.get("formats"):
            # yt-dlp-style raw_info — use yt-dlp to download
            print("[*] TikTok: using yt-dlp download path")
            return self._dl_tiktok_ytdlp(info, folder)

        vid_id = d.get("id", "tt")
        images = d.get("images")

        # Slideshow / photo post
        if images:
            output_folder = os.path.join(folder, f"slideshow_{vid_id}")
            os.makedirs(output_folder, exist_ok=True)
            total_size = 0
            for i, url in enumerate(images):
                self._ensure_not_cancelled()
                pct = (i / len(images)) * 100
                if self.progress_callback:
                    self.progress_callback(pct, "—", f"Photo {i+1}/{len(images)}")
                r = requests.get(url, stream=True, timeout=30)
                if r.status_code == 200:
                    p = os.path.join(output_folder, f"image_{i+1:03d}.jpg")
                    with open(p, "wb") as fh:
                        for chunk in r.iter_content(8192):
                            self._ensure_not_cancelled()
                            fh.write(chunk)
                    total_size += os.path.getsize(p)
            return {"file": output_folder, "size": total_size}

        # Video: try tikwm URLs first
        vurl = d.get("hdplay") or d.get("play") or d.get("wmplay")

        if not vurl:
            # Fall back to yt-dlp if no URL in raw_info
            print("[*] TikTok: no direct URL in raw_info, falling back to yt-dlp")
            return self._dl_tiktok_ytdlp(info, folder)

        title = re.sub(r'[^\w\- ]', '', d.get("title", vid_id)[:40]).strip() or vid_id
        path  = os.path.join(folder, f"{title}_{vid_id}.mp4")

        try:
            r = requests.get(
                vurl,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"},
                stream=True, timeout=60
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            total_b = int(r.headers.get("content-length", 0))
            done = 0
            with open(path, "wb") as fh:
                for chunk in r.iter_content(8192):
                    self._ensure_not_cancelled()
                    if chunk:
                        fh.write(chunk)
                        done += len(chunk)
                        if total_b and self.progress_callback:
                            pct = (done / total_b) * 100
                            self.progress_callback(pct, f"{done/1048576:.1f}MB/s", f"{done/1048576:.1f}/{total_b/1048576:.1f}MB")
            print(f"[+] TikTok video saved via tikwm: {path}")
            return {"file": path, "size": os.path.getsize(path)}

        except Exception as e:
            print(f"[!] tikwm direct download failed ({e}), trying yt-dlp fallback…")
            return self._dl_tiktok_ytdlp(info, folder)

    def _dl_tiktok_ytdlp(self, info: MediaInfo, folder: str) -> dict:
        """Download TikTok via yt-dlp (fallback path)."""
        opts = {
            **get_ytdlp_common_opts(),
            "outtmpl":          os.path.join(folder, "%(title)s.%(ext)s"),
            "progress_hooks":   [self._ytdlp_hook],
            "quiet":            True,
            "no_warnings":      True,
            "format":           "best",
            "retries":          5,
            "fragment_retries": 5,
            "socket_timeout":   30,
            "logger":           YTDLPLogger(),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([info.url])
        except Exception as e:
            raise RuntimeError(f"TikTok yt-dlp download failed: {e}") from e

        f = self._get_latest_file(folder)
        return {"file": f, "size": os.path.getsize(f) if f else 0}

    # ── Pinterest download ────────────────────
    def _dl_pinterest(self, info: MediaInfo, folder: str) -> dict:
        self._ensure_not_cancelled()

        # Always try yt-dlp first (handles both video and image pins)
        try:
            result = self._dl_ytdlp(info, "Best", folder, "MP4 · H.264")
            if result.get("file") and os.path.exists(result["file"]):
                return result
        except Exception as e:
            print(f"[!] Pinterest yt-dlp attempt failed ({e}), trying direct image download…")

        # Fallback: direct image download
        raw = info.raw_info or {}
        media_url = raw.get("url") or info.thumbnail_url
        if not media_url:
            raise RuntimeError("No media URL found for this Pinterest pin")

        ext = ".jpg"
        for e in (".mp4", ".mov", ".webm", ".png", ".gif", ".webp"):
            if e in media_url.lower():
                ext = e
                break

        title = re.sub(r'[^\w\- ]', '', info.title[:40]).strip() or "pinterest_pin"
        fname = f"{title}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
        path  = os.path.join(folder, fname)

        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.pinterest.com/"}
        r = requests.get(media_url, headers=hdrs, stream=True, timeout=60)
        r.raise_for_status()
        total_b = int(r.headers.get("content-length", 0))
        done = 0
        with open(path, "wb") as fh:
            for chunk in r.iter_content(8192):
                self._ensure_not_cancelled()
                fh.write(chunk)
                done += len(chunk)
                if total_b and self.progress_callback:
                    pct = (done / total_b) * 100
                    self.progress_callback(pct, "—", f"{done/1048576:.1f}/{total_b/1048576:.1f}MB")

        return {"file": path, "size": os.path.getsize(path)}

    # ── Utility ───────────────────────────────
    def _get_latest_file(self, folder: str) -> str | None:
        try:
            files = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
            return max(files, key=os.path.getmtime) if files else None
        except:
            return None
