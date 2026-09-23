import os
import sys

# ─── WebView2 / Chromium CPU & RAM optimisation flags ───────────────────────
# These are passed directly to the underlying Chromium renderer process.
# Applied BEFORE any import of pywebview so they take effect on startup.
_FLAGS = " ".join([
    # Disable GPU hardware acceleration (we render on CPU via WebView2's SW mode)
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-gpu-rasterization",
    "--disable-gpu-sandbox",
    "--disable-software-rasterizer",

    # V8 heap cap — keeps JS memory budget low (128 MB)
    "--js-flags=--max-old-space-size=128",

    # Renderer & timer throttling — reduce background CPU burn
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-hang-monitor",

    # Disable unused Chromium subsystems
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--disable-logging",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--no-first-run",
    "--no-default-browser-check",
    "--metrics-recording-only",
    "--autoplay-policy=no-user-gesture-required",

    # Single process for the app content — fewer OS threads
    "--process-per-site",
])
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = _FLAGS


def main() -> None:
    from native_app import main as native_main
    native_main()


if __name__ == "__main__":
    main()
