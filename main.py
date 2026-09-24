import os
import sys

# ─── WebView2 / Chromium arguments ──────────────────────────────────────────
# Keep flags minimal and safe to prevent crashes, black screens, or OOM terminates.
_FLAGS = " ".join([
    "--disable-features=TranslateUI",
    "--no-first-run",
    "--no-default-browser-check",
    "--autoplay-policy=no-user-gesture-required",
])
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = _FLAGS


def main() -> None:
    from native_app import main as native_main
    native_main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-m", "-c", "--version"):
        sys.exit(0)
    main()
