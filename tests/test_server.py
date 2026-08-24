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
    assert b"<title>Kosh" in body
    assert b"Settlement reconciliation" in body


def test_the_page_pulls_in_nothing_from_the_network(live):
    """Local-first means the UI renders with the machine unplugged."""
    _, _ = live
    page = (Path(__file__).resolve().parents[1]
            / "src/kosh/static/app.html").read_text()
    for scheme in ("http://", "https://", "//fonts.", "cdn."):
        assert scheme not in page, f"page reaches out to {scheme}"


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
