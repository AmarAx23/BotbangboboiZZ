"""Thin wrapper around the free thum.io URL-to-image API (no signup, no API
key, 1000 free screenshots/month - https://www.thum.io) - used to attach a
picture of the web report page (report_page.py) directly in the LINE chat,
right after the bot's text reply, instead of making people tap a link to
see it.

No server-side rendering on our end - that would need a headless browser
(Playwright/Chromium), which is heavy to run on Render's free tier. thum.io
renders the URL on their servers and hands back a plain image we can point
LINE's ImageMessage straight at."""

_THUMIO_BASE = "https://image.thum.io/get"


def report_screenshot_urls(page_url: str):
    """Returns (original_url, preview_url) - both plain HTTPS image URLs,
    suitable for a LINE ImageMessage's originalContentUrl/previewImageUrl.

    `noanimate` makes thum.io block and return the final render instead of
    streaming a "loading..." placeholder first - we only ever fetch once
    (LINE's servers download it right after we send the message), so we
    want the real thing, not a spinner frame."""
    original = f"{_THUMIO_BASE}/width/1000/noanimate/{page_url}"
    preview = f"{_THUMIO_BASE}/width/240/noanimate/{page_url}"
    return original, preview
