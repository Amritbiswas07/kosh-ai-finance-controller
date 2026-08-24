"""A local web front end for the reconciliation engine.

Standard library only — `ThreadingHTTPServer`, not FastAPI. The engine's whole
posture is that it adds nothing it cannot justify, and a web demo does not
justify a dependency tree. It binds to localhost, serves one self-contained
page with no CDN links, and calls exactly the same `reconcile()` the CLI does.

A run with the model enabled takes ~10 s, so `/api/run` streams the pipeline
stage by stage over Server-Sent Events rather than leaving a spinner up.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .ask import answer
from .evaluate import evaluate
from .generate import build, write
from .ingest import build_batches, load
from .match import reconcile
from .position import bridge_rows, build_position
from .report import TIER_MEANING, json_report
from .schema import EXCEPTION_MEANING

STATIC = Path(__file__).resolve().parent / "static"
LOGO = STATIC / "razorpay-logo.svg"
ROOT = Path(__file__).resolve().parents[2]

def page_html() -> bytes:
    """The page, with the brand mark inlined if one has been supplied.

    Inlined rather than referenced with `<img src>` on purpose: only an SVG that
    is part of the document can inherit `currentColor`, which is what lets the
    dark half of the Razorpay mark invert with the theme instead of disappearing
    into the navy background. It also keeps the page a single request.
    """
    html = (STATIC / "app.html").read_text()
    if not LOGO.is_file():
        return html.encode()
    svg = LOGO.read_text()
    svg = re.sub(r"<\?xml[^>]*\?>\s*", "", svg).strip()
    return (html
            .replace("<!--RAZORPAY_LOGO-->", svg)
            .replace('<span class="mark" id="fallbackmark">K</span>', "")
            .encode())


#: One run at a time. The generator writes to a shared directory and the model
#: is not re-entrant, so two overlapping requests would interleave inside both.
_LOCK = threading.Lock()
#: Loading the weights needs its own lock, held *outside* `_LOCK`. An ask that
#: arrived while a run was in flight put two threads into
#: `from_pretrained(...).to(device)` at once, and torch failed the second with
#: "Cannot copy out of meta tensor" — a confusing error a long way from its cause.
_MODEL_LOCK = threading.Lock()


class Session:
    """Whatever the last run produced, plus the lazily-loaded model."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.state: dict | None = None
        self.seed = 20260824
        self._adjudicator = None

    def adjudicator(self):
        """Load the weights at most once, and never from two threads at once."""
        if self._adjudicator is not None:
            return self._adjudicator
        with _MODEL_LOCK:
            if self._adjudicator is None:            # re-check inside the lock
                from .llm import LocalAdjudicator
                adj = LocalAdjudicator()
                adj.load()
                self._adjudicator = adj              # only on success
        return self._adjudicator

    # The live objects, kept so /api/ask can query the run it just did.
    result = None
    position = None

    def run(self, seed: int | None, use_llm: bool, emit) -> dict:
        if seed is not None and seed != self.seed:
            emit("generate", f"Generating corpus from seed {seed}")
            ds, gt, inj = build(seed=seed)
            write(ds, gt, inj, self.data_dir, seed)
            self.seed = seed

        adj = None
        if use_llm:
            warm = self._adjudicator is not None
            emit("model", "Model already loaded" if warm else "Loading local model")
            adj = self.adjudicator()
            adj.calls, adj.seconds = 0, 0.0

        emit("ingest", "Reading the three sources")
        t0 = time.perf_counter()
        ds, errors = load(self.data_dir)
        batches = build_batches(ds)

        emit("match", f"Reconciling {len(ds):,} records across four legs")
        res = reconcile(ds, batches, adj)
        wall = time.perf_counter() - t0

        emit("position", "Building the cash position")
        pos = build_position(ds, batches, res)

        metrics = None
        gt_path = self.data_dir / "ground_truth.json"
        if gt_path.exists():
            emit("evaluate", "Scoring against held-out ground truth")
            metrics = evaluate(res, ds, gt_path, wall)

        llm_seconds = round(getattr(adj, "seconds", 0.0), 3) if adj else 0.0
        meta = {"seed": self.seed, "llm_seconds": llm_seconds,
                "model": (adj.name if adj else "no model (deterministic only)"),
                "wall_seconds": round(wall, 3), "ingest_errors": errors}
        payload = json.loads(json_report(res, pos, metrics, meta))
        payload["bridge"] = [{"label": l, "amount": a, "kind": k}
                             for l, a, k in bridge_rows(pos)]
        payload["tier_meaning"] = TIER_MEANING
        payload["exception_meaning"] = {c.value: m for c, m in EXCEPTION_MEANING.items()}
        payload["counts"] = res.counts

        self.state, self.result, self.position = payload, res, pos
        return payload


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):        # quieter than the default
            pass

        # ------------------------------------------------------------ helpers
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return {}

        # --------------------------------------------------------------- GET
        def do_GET(self) -> None:
            try:
                self._get()
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": str(exc)}, 500)

        def _get(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, page_html(), "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                # basename only: no traversal out of the static directory.
                name = Path(path).name
                target = STATIC / name
                if not name or not target.is_file():
                    self._json({"error": "not found"}, 404)
                    return
                kind = ("image/svg+xml" if name.endswith(".svg")
                        else "image/png" if name.endswith(".png")
                        else "application/octet-stream")
                self._send(200, target.read_bytes(), kind)
            elif path == "/api/state":
                if session.state is None:
                    self._json({"ready": False})
                else:
                    self._json({"ready": True, **session.state})
            else:
                self._json({"error": "not found"}, 404)

        # -------------------------------------------------------------- POST
        def do_POST(self) -> None:
            try:
                self._post()
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": str(exc)}, 500)

        def _post(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/run":
                self._stream_run()
            elif path == "/api/ask":
                self._ask()
            else:
                self._json({"error": "not found"}, 404)

        def _ask(self) -> None:
            body = self._read_json()
            question = (body.get("question") or "").strip()
            if not question:
                self._json({"error": "no question"}, 400)
                return
            if session.result is None:
                self._json({"error": "run a reconciliation first"}, 409)
                return
            try:
                model = session.adjudicator() if body.get("llm") else None
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": f"could not load the model: {exc}"}, 503)
                return
            with _LOCK:
                out = answer(question, session.result, session.position, model)
            self._json(out)

        def _stream_run(self) -> None:
            """Server-Sent Events, one message per pipeline stage.

            `Connection: close` is load-bearing. With no Content-Length and no
            chunked encoding, closing the socket is the browser's only
            end-of-stream signal; on keep-alive the reader never reports done
            and the page stays stuck showing the first run as still going.
            """
            qs = parse_qs(urlparse(self.path).query)
            seed = int(qs["seed"][0]) if qs.get("seed") else None
            use_llm = qs.get("llm", ["0"])[0] == "1"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(stage: str, message: str) -> None:
                self.wfile.write(
                    f"data: {json.dumps({'stage': stage, 'message': message})}\n\n"
                    .encode())
                self.wfile.flush()

            try:
                with _LOCK:
                    payload = session.run(seed, use_llm, emit)
                self.wfile.write(
                    f"data: {json.dumps({'stage': 'done', 'result': payload})}\n\n"
                    .encode())
            except Exception as exc:                       # surfaced, not swallowed
                traceback.print_exc()
                self.wfile.write(
                    f"data: {json.dumps({'stage': 'error', 'message': str(exc)})}\n\n"
                    .encode())
            self.wfile.flush()

    return Handler


def serve(data_dir: Path, host: str = "127.0.0.1", port: int = 8000,
          preload: bool = True) -> None:
    session = Session(data_dir)
    if not (data_dir / "erp_invoices.csv").exists():
        print(f"No corpus at {data_dir} — generating seed {session.seed}")
        ds, gt, inj = build(seed=session.seed)
        write(ds, gt, inj, data_dir, session.seed)
    if preload:
        session.run(None, False, lambda *a: None)          # first paint is instant
    httpd = ThreadingHTTPServer((host, port), make_handler(session))
    print(f"Kosh is running at http://{host}:{port}")
    print("The first view is a deterministic run. Toggle the model in the header.")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.server_close()
