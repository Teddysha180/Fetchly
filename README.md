# Fetchly - Native Desktop Edition

A sleek, high-performance media downloader built with Python, PyWebView, and yt-dlp.

## Project Structure
```text
Fetchly/
├── main.py               # App entry point
├── native_app.py         # PyWebView desktop bridge & window management
├── downloader_engine.py  # yt-dlp + FFmpeg backend
├── fetchly.html          # Fetchly UI (Gold & Obsidian theme)
└── requirements.txt      # Python dependencies
```

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the app:
   ```bash
   python main.py
   ```

## Build EXE

```bash
pyinstaller --noconsole --onefile --name "Fetchly" main.py
```

## Notes

- The app no longer uses WebView2 or HTML as the main interface.
- Downloads still use the existing `yt-dlp` + FFmpeg backend.
- YouTube links open in the default browser when needed.
- The UI is intentionally kept dark and lightweight for low-end PCs.
