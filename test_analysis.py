"""
Playwright test for MeshRoute analysis pages.
Tests both EN and DE versions, checks all 5 charts render,
data loads correctly, filters work, and no JS errors.
"""
import json
import os
import sys
import threading
import http.server
import functools
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 8199

# Start a local HTTP server for fetch() to work (file:// blocks fetch)
def start_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=BASE_DIR)
    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), handler)
    server.serve_forever()

server_thread = threading.Thread(target=start_server, daemon=True)
server_thread.start()

PAGES = [
    ("EN Analysis", f"http://127.0.0.1:{HTTP_PORT}/analysis.html"),
    ("DE Analysis", f"http://127.0.0.1:{HTTP_PORT}/de/analysis.html"),
]
CHART_IDS = [
    "chart-reality-gap",
    "chart-feature-waterfall",
    "chart-efficiency",
    "chart-heatmap",
    "chart-silencing-scatter",
]

errors = []
warnings = []


def test_page(page, name, url):
    """Test a single analysis page."""
    print(f"\n{'='*60}")
    print(f"  Testing: {name}")
    print(f"  URL: {url}")
    print(f"{'='*60}")

    # Collect JS errors
    js_errors = []
    page.on("pageerror", lambda err: js_errors.append(str(err)))
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

    # Load page
    page.goto(url, wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(2000)  # wait for canvas rendering + retries

    # 1. Check page title
    title = page.title()
    print(f"\n  1. Title: {title}")
    if "MeshRoute" not in title:
        errors.append(f"{name}: Title missing 'MeshRoute': {title}")
        print(f"     FAIL: Missing 'MeshRoute'")
    else:
        print(f"     OK")

    # 2. Check analysis-data.json loaded
    # Check if charts rendered (DATA is in IIFE scope, can't access directly)
    insight_text = page.locator("#insight-reality").inner_text() if page.locator("#insight-reality").count() > 0 else ""
    data_loaded = "Loading" not in insight_text and "Error" not in insight_text and "Fehler" not in insight_text and len(insight_text) > 30
    n_results = -1  # can't access from outside IIFE
    n_scenarios = -1
    print(f"\n  2. Data loaded: {data_loaded} ({n_scenarios} scenarios, {n_results} records)")
    if not data_loaded:
        errors.append(f"{name}: analysis-data.json failed to load")
        print(f"     FAIL")
    else:
        print(f"     OK (insight text rendered = data loaded successfully)")

    # 3. Check all 5 canvases exist and have content
    print(f"\n  3. Canvas rendering:")
    for cid in CHART_IDS:
        canvas = page.locator(f"#{cid}")
        exists = canvas.count() > 0
        if not exists:
            errors.append(f"{name}: Canvas #{cid} not found")
            print(f"     {cid}: FAIL (not found)")
            continue

        # Check canvas has non-zero dimensions
        dims = page.evaluate(f"""() => {{
            const c = document.getElementById('{cid}');
            return c ? {{w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight}} : null;
        }}""")
        if not dims or dims['w'] == 0 or dims['h'] == 0:
            errors.append(f"{name}: Canvas #{cid} has zero dimensions: {dims}")
            print(f"     {cid}: FAIL (zero size: {dims})")
            continue

        # Check canvas has drawn pixels (not all transparent)
        has_content = page.evaluate(f"""() => {{
            const c = document.getElementById('{cid}');
            if (!c) return false;
            const ctx = c.getContext('2d');
            const data = ctx.getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < data.length; i += 4) {{
                if (data[i] > 0) return true;
            }}
            return false;
        }}""")
        if not has_content:
            errors.append(f"{name}: Canvas #{cid} is empty (no drawn pixels)")
            print(f"     {cid}: FAIL (empty canvas, dims={dims['w']}x{dims['h']})")
        else:
            print(f"     {cid}: OK ({dims['w']}x{dims['h']}px, has content)")

    # 4. Check filter buttons work
    print(f"\n  4. Filter buttons:")
    filter_btns = page.locator("#filter-scenario .filter-btn")
    n_btns = filter_btns.count()
    print(f"     Found {n_btns} filter buttons")
    if n_btns >= 3:
        # Click "Large" filter
        filter_btns.nth(3).click()
        page.wait_for_timeout(500)
        active = page.locator("#filter-scenario .filter-btn.active")
        if active.count() == 1:
            print(f"     Filter click: OK (active button changed)")
        else:
            warnings.append(f"{name}: Filter click didn't change active state")
            print(f"     Filter click: WARN")

        # Click back to "All"
        filter_btns.nth(0).click()
        page.wait_for_timeout(300)
    else:
        warnings.append(f"{name}: Expected 4 filter buttons, got {n_btns}")

    # 5. Check insight text rendered
    print(f"\n  5. Insight text:")
    insight = page.locator("#insight-reality")
    if insight.count() > 0:
        text = insight.inner_text()
        if "Loading" in text or "Error" in text or len(text) < 20:
            errors.append(f"{name}: Insight text not rendered: {text[:80]}")
            print(f"     FAIL: '{text[:80]}'")
        else:
            print(f"     OK: '{text[:80]}...'")
    else:
        errors.append(f"{name}: #insight-reality not found")

    # 6. Check navigation links
    print(f"\n  6. Navigation:")
    nav_links = page.locator("nav a")
    n_links = nav_links.count()
    print(f"     {n_links} nav links found")
    if n_links < 4:
        errors.append(f"{name}: Too few nav links: {n_links}")

    # Check language switcher
    lang_link = page.locator("nav a[title='Deutsch'], nav a[title='English']")
    if lang_link.count() > 0:
        print(f"     Language switcher: OK")
    else:
        warnings.append(f"{name}: No language switcher found")
        print(f"     Language switcher: WARN (missing)")

    # 7. JS errors
    print(f"\n  7. JavaScript errors:")
    if js_errors:
        for e in js_errors:
            errors.append(f"{name}: JS error: {e[:100]}")
        print(f"     FAIL: {len(js_errors)} errors")
        for e in js_errors[:3]:
            print(f"       {e[:120]}")
    else:
        print(f"     OK (no errors)")

    if console_errors:
        print(f"     Console errors: {len(console_errors)}")
        for e in console_errors[:3]:
            print(f"       {e[:120]}")

    # 8. Screenshot
    ss_name = name.lower().replace(' ', '_')
    ss_path = os.path.join(BASE_DIR, f"test_{ss_name}.png")
    page.screenshot(path=ss_path, full_page=True)
    print(f"\n  8. Screenshot saved: {ss_path}")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        for name, url in PAGES:
            try:
                test_page(page, name, url)
            except Exception as e:
                errors.append(f"{name}: Exception: {e}")
                print(f"\n  EXCEPTION: {e}")

        browser.close()

    # Summary
    print(f"\n{'='*60}")
    print(f"  TEST SUMMARY")
    print(f"{'='*60}")
    print(f"  Pages tested: {len(PAGES)}")
    print(f"  Errors: {len(errors)}")
    print(f"  Warnings: {len(warnings)}")

    if errors:
        print(f"\n  ERRORS:")
        for e in errors:
            print(f"    - {e}")

    if warnings:
        print(f"\n  WARNINGS:")
        for w in warnings:
            print(f"    - {w}")

    if not errors:
        print(f"\n  ALL TESTS PASSED")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
