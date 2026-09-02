"""PWA acceptance tests — DESIGN.md §7 row AC7 / §5.

Covers the HTTP-level checks: manifest fields + MIME, SW precache list
resolvability (every PRECACHE URL must 200 or install/addAll fails), icon
files exist at the referenced paths with exact pixel dimensions (Pillow), and
the §5.4 meta tags present in index.html. All static responses carry
`Cache-Control: no-cache` (§1.3, §8.7).
"""

import re
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

# Every entry the service worker precaches (§5.2) — must resolve or install fails.
EXPECTED_PRECACHE = [
    "/",
    "/index.html",
    "/style.css",
    "/app.js",
    "/vendor/sortable.min.js",
    "/manifest.webmanifest",
    "/icons/icon-32.png",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/icon-maskable-512.png",
    "/icons/apple-touch-icon.png",
]

# Referenced icons: path -> (width, height, mode per §5.3: RGBA except
# apple-touch-icon which is full-bleed RGB).
EXPECTED_ICONS = {
    "/icons/icon-32.png": (32, 32, "RGBA"),
    "/icons/icon-192.png": (192, 192, "RGBA"),
    "/icons/icon-512.png": (512, 512, "RGBA"),
    "/icons/icon-maskable-512.png": (512, 512, "RGBA"),
    "/icons/apple-touch-icon.png": (180, 180, "RGB"),
}

CONTENT_TYPES = {
    "/": "text/html",
    "/index.html": "text/html",
    "/style.css": "text/css",
    "/app.js": "text/javascript",
    "/manifest.webmanifest": "application/manifest+json",
    "/icons/icon-32.png": "image/png",
    "/icons/icon-192.png": "image/png",
    "/icons/icon-512.png": "image/png",
    "/icons/icon-maskable-512.png": "image/png",
    "/icons/apple-touch-icon.png": "image/png",
}


def test_manifest_required_fields(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/manifest+json"
    mf = r.json()
    assert mf["name"] == "Taskflow"
    assert mf["short_name"] == "Tasks"
    assert mf["start_url"] == "/"
    assert mf["scope"] == "/"
    assert mf["display"] == "standalone"
    assert mf["theme_color"] == "#17171B"
    assert mf["background_color"] == "#17171B"
    assert mf["lang"] == "en" and mf["dir"] == "ltr"
    assert isinstance(mf["description"], str) and mf["description"]

    icons = mf["icons"]
    assert any(i["sizes"] == "192x192" and i["purpose"] == "any"
               and i["type"] == "image/png" for i in icons)
    assert any(i["sizes"] == "512x512" and i["purpose"] == "any"
               and i["type"] == "image/png" for i in icons)
    assert any(i["sizes"] == "512x512" and i["purpose"] == "maskable"
               and i["type"] == "image/png" for i in icons)
    # every manifest icon exists on disk at the stated size (covered per-icon
    # below, but fail fast here for src typos)
    for icon in icons:
        assert (STATIC_DIR / icon["src"].lstrip("/")).is_file(), icon["src"]


def test_sw_precache_entries_resolvable(client):
    """Each PRECACHE URL -> 200 with the right media type; install's addAll
    would reject otherwise. Static responses carry Cache-Control: no-cache."""
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers.get("cache-control") == "no-cache"
    text = r.text
    assert "taskflow-shell-v15" in text and "taskflow-api-v1" in text

    m = re.search(r"const PRECACHE\s*=\s*\[(.*?)\];", text, re.S)
    assert m, "PRECACHE array not found in sw.js"
    precache = re.findall(r"'([^']+)'", m.group(1))
    assert precache == EXPECTED_PRECACHE, precache

    for path in precache:
        rr = client.get(path)
        assert rr.status_code == 200, f"PRECACHE entry {path} -> {rr.status_code}"
        assert rr.headers.get("cache-control") == "no-cache", path
        expected = CONTENT_TYPES.get(path)
        if expected:
            assert rr.headers["content-type"].startswith(expected), \
                (path, rr.headers["content-type"])
        if path.endswith(".png"):
            assert rr.content[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"


def test_icon_files_exist_and_dimensions(client):
    for url, (w, h, mode) in EXPECTED_ICONS.items():
        disk = STATIC_DIR / url.lstrip("/")
        assert disk.is_file(), f"missing icon on disk: {disk}"
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        with Image.open(disk) as img:
            assert img.size == (w, h), (url, img.size)
            assert img.mode == mode, (url, img.mode)


def test_index_meta_tags(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text

    # §5.4 exact meta/link set
    assert '<meta charset="utf-8">' in html
    assert 'name="viewport"' in html and "viewport-fit=cover" in html
    assert "<title>Taskflow</title>" in html
    assert 'name="description"' in html
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'name="theme-color" content="#17171B" media="(prefers-color-scheme: dark)"' in html
    assert 'name="theme-color" content="#FAFAFB" media="(prefers-color-scheme: light)"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-status-bar-style" content="black-translucent"' in html
    assert 'name="apple-mobile-web-app-title" content="Tasks"' in html
    assert '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">' in html
    assert 'href="/icons/icon-32.png"' in html
    assert 'href="/icons/icon-192.png"' in html
    assert html.count('name="theme-color"') == 2


def test_sw_and_shell_no_cache_headers(client):
    """sw.js and the shell must never be served stale (§1.3, §8.7)."""
    for path in ("/sw.js", "/index.html", "/", "/manifest.webmanifest"):
        rr = client.get(path)
        assert rr.status_code == 200
        assert rr.headers.get("cache-control") == "no-cache", path
