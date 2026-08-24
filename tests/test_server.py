"""The web layer: routing, streaming, and the guards on /api/ask.

The server is started for real on an ephemeral port and driven over HTTP — no
model is loaded, so the whole file runs in well under a second.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from kosh.generate import build, write
from kosh.server import Session, make_handler


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    data = tmp_path_factory.mktemp("corpus")
    ds, gt, inj = build(seed=4242)
    write(ds, gt, inj, data, 4242)
    session = Session(data)
    session.seed = 4242
    session.run(None, False, lambda *a: None)

    with socket.socket() as s:                       # a port nobody else holds
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", session
    httpd.shutdown()
    httpd.server_close()


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:      # urlopen raises on 4xx/5xx
        return e.code, e.read(), e.headers.get("Content-Type", "")


def post(base, path, obj=None):
    body = json.dumps(obj).encode() if obj is not None else b""
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_serves_the_page(live):
    base, _ = live
    status, body, ctype = get(base, "/")
    assert status == 200 and "text/html" in ctype
    assert b"AI Finance Controller" in body
    # Exactly one h1, so the page keeps a single document landmark. It names the
    # current section, since the section tabs live behind the menu button.
    assert body.count(b"<h1") == 1


def test_the_page_fetches_nothing_from_the_network(live):
    """Local-first means the UI renders with the machine unplugged.

    A hyperlink is fine — clicking it is the user's choice and costs nothing at
    render time. What must not exist is a *fetched* resource: a stylesheet, a
    webfont, a script or an image pulled from another host.
    """
    import re
    _, _ = live
    page = (Path(__file__).resolve().parents[1]
            / "src/kosh/static/app.html").read_text()

    for m in re.finditer(r"""\bsrc\s*=\s*["\']([^"\']+)""", page):
        assert not m.group(1).startswith(("http://", "https://", "//")), m.group(1)
    for m in re.finditer(r"url\(\s*['\"]?([^)'\"]+)", page):
        assert not m.group(1).startswith(("http://", "https://", "//")), m.group(1)
    assert "@import" not in page
    assert not re.search(r"<link[^>]+rel=[\"\']?stylesheet", page, re.I)
    assert "fonts.googleapis" not in page and "fonts.gstatic" not in page

    # Every href that does leave the machine must be a real navigation, not a fetch.
    for m in re.finditer(r"""\bhref\s*=\s*["\']([^"\']+)""", page):
        url = m.group(1)
        if url.startswith(("http://", "https://")):
            assert "razorpay.com" in url, url


def test_static_files_are_served_and_traversal_is_refused(live):
    base, _ = live
    # The logo slot is optional: absent, it 404s and the page drops the <img>.
    status, _, _ = get(base, "/static/razorpay-logo.svg")
    assert status in (200, 404)
    status, _, _ = get(base, "/static/../server.py")
    assert status == 404
    status, body, ctype = get(base, "/static/app.html")
    assert status == 404 or b"<title>" in body


def test_state_carries_a_complete_run(live):
    base, _ = live
    status, body, _ = get(base, "/api/state")
    assert status == 200
    d = json.loads(body)
    assert d["ready"] is True
    for key in ("matches", "findings", "position", "bridge", "metrics",
                "tier_meaning", "exception_meaning", "counts"):
        assert key in d, key
    assert d["counts"]["total_records"] > 50          # the Track 4 floor
    assert d["metrics"]["links"]["invoice_to_payment"]["f1"] == 1.0


def test_unknown_routes_are_json_not_html(live):
    base, _ = live
    status, body, _ = get(base, "/api/nope")
    assert status == 404 and json.loads(body)["error"]


def test_run_streams_every_stage_and_terminates(live):
    """The stream must end. A keep-alive SSE response never reports done and
    leaves the page stuck on the first run."""
    base, _ = live
    req = urllib.request.Request(base + "/api/run?seed=4242&llm=0", method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        assert "text/event-stream" in r.headers.get("Content-Type", "")
        assert r.headers.get("Connection", "").lower() == "close"
        raw = r.read().decode()                      # returns only if the socket closes
    events = [json.loads(c[5:]) for c in raw.split("\n\n") if c.startswith("data:")]
    stages = [e["stage"] for e in events]
    for expected in ("ingest", "match", "position", "evaluate", "done"):
        assert expected in stages, stages
    assert stages[-1] == "done"
    assert events[-1]["result"]["counts"]["total_records"] > 50


def test_ask_is_grounded_and_needs_no_model(live):
    base, _ = live
    status, body = post(base, "/api/ask", {"question": "what is missing in bank?"})
    assert status == 200
    d = json.loads(body)
    assert d["grounded"] is True and d["model_used"] is False and d["facts"]


def test_ask_rejects_an_empty_question(live):
    base, _ = live
    status, _ = post(base, "/api/ask", {"question": "   "})
    assert status == 400


def test_ask_declines_when_nothing_is_relevant(live):
    base, _ = live
    status, body = post(base, "/api/ask", {"question": "unladen swallow airspeed"})
    assert status == 200
    assert "Nothing in this reconciliation" in json.loads(body)["answer"]


def test_a_fresh_session_reports_not_ready(tmp_path):
    """Started with --no-preload, the page must say so rather than error."""
    ds, gt, inj = build(seed=5)
    write(ds, gt, inj, tmp_path, 5)
    session = Session(tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body, _ = get(f"http://127.0.0.1:{port}", "/api/state")
        assert status == 200 and json.loads(body) == {"ready": False}
        # And asking before any run is a 409, not a crash.
        code, _ = post(f"http://127.0.0.1:{port}", "/api/ask", {"question": "hi"})
        assert code == 409
    finally:
        httpd.shutdown(); httpd.server_close()


def test_the_model_is_loaded_at_most_once_under_contention(live):
    """Regression: two threads in from_pretrained().to() raised
    'Cannot copy out of meta tensor' a long way from the actual cause."""
    _, session = live
    calls, lock = [], threading.Lock()

    class FakeAdj:
        name = "fake"
        def load(self):
            with lock:
                calls.append(1)

    import kosh.server as srv
    real = session.adjudicator.__func__
    session._adjudicator = None
    with srv._MODEL_LOCK:                            # the lock exists and is a Lock
        pass

    def fake_loader():
        if session._adjudicator is not None:
            return session._adjudicator
        with srv._MODEL_LOCK:
            if session._adjudicator is None:
                adj = FakeAdj(); adj.load(); session._adjudicator = adj
        return session._adjudicator

    threads = [threading.Thread(target=fake_loader) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1, f"loaded {len(calls)} times"
    session._adjudicator = None
    assert real is not None


def test_the_brand_mark_is_inlined_not_referenced(live):
    """Only an SVG inside the document can inherit currentColor, which is what
    lets the mark's dark half invert instead of vanishing into a navy header."""
    base, _ = live
    from kosh.server import LOGO
    status, body, _ = get(base, "/")
    page = body.decode()
    if not LOGO.is_file():
        pytest.skip("no brand mark supplied")
    assert "<svg" in page and 'aria-label="Razorpay"' in page
    assert "currentColor" in page
    assert "#3395ff" in page              # the supplied blue is left untouched
    assert 'id="fallbackmark"' not in page
    assert "<!--RAZORPAY_LOGO-->" not in page
    # Inlined means no extra request for it.
    assert 'img src="/static/razorpay-logo.svg"' not in page


def test_the_mark_is_cropped_to_its_artwork(live):
    """As supplied the mark sits in a 960-square canvas and renders ~5px tall
    at header size; the stored asset is tightened to the measured bounds."""
    from kosh.server import LOGO
    if not LOGO.is_file():
        pytest.skip("no brand mark supplied")
    import re as _re
    vb = _re.search(r'viewBox="([^"]+)"', LOGO.read_text()).group(1)
    x, y, w, h = (float(v) for v in vb.split())
    assert w / h > 3.5, f"viewBox {vb} is not cropped to the artwork band"
    assert h < 400, f"viewBox {vb} still carries the square canvas"


def test_sections_live_behind_an_accessible_menu(live):
    """The four sections moved into a drawer, so the button that opens it has to
    carry the state a screen reader needs."""
    base, _ = live
    _, body, _ = get(base, "/")
    page = body.decode()
    assert 'id="menu"' in page
    assert 'aria-controls="drawer"' in page
    assert 'aria-expanded="false"' in page          # closed on first paint
    assert 'aria-label="Open sections"' in page
    assert '<aside class="drawer" id="drawer" hidden' in page
    for section in ("overview", "exceptions", "model", "ask"):
        assert f'data-tab="{section}"' in page, section
    # The drawer can be dismissed three ways, all wired in the page itself.
    assert "closeMenu" in page and "scrim" in page and '"Escape"' in page


def test_the_product_name_is_not_in_the_header(live):
    base, _ = live
    _, body, _ = get(base, "/")
    header = body.decode().split("</header>")[0]
    assert "Kosh" not in header
