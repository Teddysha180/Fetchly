# Fetchly — Native Desktop Downloader

A sleek, high-performance media downloader built with Python, PyWebView, and yt-dlp.  
Supports YouTube, TikTok, Instagram, Facebook, Twitter/X, Vimeo, Pinterest and more.

## Project Structure
```text
Fetchly/
├── main.py               # App entry point
├── native_app.py         # PyWebView desktop bridge & window management
├── downloader_engine.py  # yt-dlp + FFmpeg backend
├── fetchly.html          # Fetchly UI (Gold & Obsidian theme)
├── updater.py            # Auto-update engine (checks GitHub on startup)
├── version.json          # Current version — bump this before every push
├── requirements.txt      # Python dependencies
├── install.bat           # One-click installer (Windows)
└── run.bat               # One-click launcher (Windows)
```

---

## Installation

### Requirements
- **Python 3.10 or newer** — https://www.python.org/downloads/  
  *(Check "Add Python to PATH" during install)*

### Option A — One-click (Windows)
1. Download or clone this repo
2. Double-click **`install.bat`** — installs all dependencies automatically
3. Double-click **`run.bat`** to launch Fetchly

### Option B — Manual
```bash
pip install -r requirements.txt
python main.py
```

---

## Auto-Update

Fetchly updates itself automatically:

| What updates | When |
|---|---|
| **Fetchly app** (UI, features) | 4 s after launch — checks GitHub for new `version.json` |
| **yt-dlp** (download engine) | On every download error AND on startup — stays current for YouTube/TikTok |

To release an update for all users:
1. Push changes to GitHub
2. Bump `"version"` in `version.json` (e.g. `"1.0.0"` → `"1.0.1"`)

---

## Build EXE (optional)

```bash
pyinstaller --noconsole --onefile --name "Fetchly" main.py
```

---

## Notes

- Downloads are saved to `~/Downloads/Fetchly/` by default
- YouTube links open in the default browser if needed
- The UI is dark and lightweight — optimised for low-end PCs
