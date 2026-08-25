"""Persistent state, so reconciliation is a control rather than a report.

Everything else in Kosh is stateless: point it at three files and it tells you
what it found. Real reconciliation is not like that. Money arrives late — a
settlement sent on Monday reaches the bank on Wednesday — so an exception raised
today is routinely answered by data that does not exist yet. A tool that starts
from nothing every morning cannot tell you that yesterday's break has cleared,
which is the single thing a controller most wants to know.

So this keeps three things between runs:

  *records*    a fingerprint of every source row ever seen, so re-loading the
               same export is a no-op rather than a double count
  *links*      matches already made, with the run that made them
  *exceptions* an open/resolved lifecycle with an age, so a break that clears on
               its own is recorded as having cleared, and one that does not gets
               visibly older

The engine stays stateless and knows nothing about this file. Reconciliation
still runs over the whole current snapshot; the store diffs one run against the
last. That keeps the matching logic — the audited part — free of hidden state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .match import Disposition, Leg, ReconResult
from .money import to_rupees
from .schema import Dataset

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  records INTEGER, new_records INTEGER,
  opened INTEGER, resolved INTEGER, carried INTEGER
);
CREATE TABLE IF NOT EXISTS record (
  key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  first_seen_run INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS link (
  leg TEXT NOT NULL, left_key TEXT NOT NULL, right_key TEXT NOT NULL,
  tier TEXT, confidence REAL, delta INTEGER, evidence TEXT,
  run_id INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (leg, left_key, right_key)
);
CREATE TABLE IF NOT EXISTS exception (
  key TEXT NOT NULL, code TEXT NOT NULL,
  source TEXT, status TEXT NOT NULL,
  value_at_risk INTEGER, evidence TEXT, proposed_action TEXT,
  opened_run INTEGER NOT NULL, opened_at TEXT NOT NULL,
  resolved_run INTEGER, resolved_at TEXT, resolution TEXT,
  PRIMARY KEY (key, code)
);
"""


def _fingerprint(obj) -> str:
    """Content hash of a source row. Two exports of the same day agree; an
    amended row does not, and is treated as new."""
    payload = json.dumps({k: str(v) for k, v in sorted(obj.__dict__.items())},
                         sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass
class SyncReport:
    run_id: int
    records: int
    new_records: int
    opened: list[tuple[str, str, int]]
    resolved: list[tuple[str, str, int, str]]
    carried: list[tuple[str, str, int, int]]      # key, code, value, age in runs
    new_links: int

    def to_json(self) -> dict:
        return {
            "run": self.run_id, "records": self.records,
            "new_records": self.new_records, "new_links": self.new_links,
            "opened": [{"key": k, "code": c, "value": str(to_rupees(v))}
                       for k, c, v in self.opened],
            "resolved": [{"key": k, "code": c, "value": str(to_rupees(v)),
                          "how": h} for k, c, v, h in self.resolved],
            "carried": [{"key": k, "code": c, "value": str(to_rupees(v)),
                         "age_runs": a} for k, c, v, a in self.carried],
        }


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ query
    def open_exceptions(self) -> dict[tuple[str, str], sqlite3.Row]:
        self.db.row_factory = sqlite3.Row
        rows = self.db.execute(
            "SELECT * FROM exception WHERE status = 'open'").fetchall()
        return {(r["key"], r["code"]): r for r in rows}

    def counts(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]     # noqa: E731
        return {"runs": q("SELECT COUNT(*) FROM run"),
                "records": q("SELECT COUNT(*) FROM record"),
                "links": q("SELECT COUNT(*) FROM link"),
                "open_exceptions": q(
                    "SELECT COUNT(*) FROM exception WHERE status='open'"),
                "resolved_exceptions": q(
                    "SELECT COUNT(*) FROM exception WHERE status='resolved'")}

    # ------------------------------------------------------------------ write
    def sync(self, ds: Dataset, res: ReconResult) -> SyncReport:
        """Fold one reconciliation into the accumulated picture."""
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.db.cursor()
        cur.execute("INSERT INTO run (started_at, records) VALUES (?, ?)",
                    (now, len(ds)))
        run_id = cur.lastrowid

        # --- records: idempotent on content -------------------------------
        new_records = 0
        for source, rows in (("erp", ds.invoices), ("pg", ds.pg), ("bank", ds.bank)):
            for r in rows:
                fp = _fingerprint(r)
                prev = cur.execute("SELECT fingerprint FROM record WHERE key = ?",
                                   (r.key,)).fetchone()
                if prev is None:
                    cur.execute("INSERT INTO record VALUES (?,?,?,?,?)",
                                (r.key, source, fp, run_id, now))
                    new_records += 1
                elif prev[0] != fp:
                    # An amended row. Re-fingerprint it and count it as new work.
                    cur.execute("UPDATE record SET fingerprint=? WHERE key=?",
                                (fp, r.key))
                    new_records += 1

        # --- links ---------------------------------------------------------
        new_links = 0
        for m in res.matches:
            for right in m.right:
                got = cur.execute(
                    "SELECT 1 FROM link WHERE leg=? AND left_key=? AND right_key=?",
                    (m.leg.value, m.left, right)).fetchone()
                if got:
                    continue
                cur.execute("INSERT INTO link VALUES (?,?,?,?,?,?,?,?,?)",
                            (m.leg.value, m.left, right, m.tier.value, m.confidence,
                             m.delta_paise, json.dumps(m.evidence), run_id, now))
                new_links += 1

        # --- exceptions: open, carry, or clear ------------------------------
        was_open = self.open_exceptions()
        now_present = {(f.key, f.code.value): f for f in res.findings
                       if f.disposition is Disposition.NEEDS_REVIEW}

        opened, carried = [], []
        for (key, code), f in now_present.items():
            if (key, code) in was_open:
                row = was_open[(key, code)]
                age = run_id - row["opened_run"]
                carried.append((key, code, f.value_at_risk_paise, age))
                cur.execute("UPDATE exception SET value_at_risk=?, evidence=?, "
                            "proposed_action=? WHERE key=? AND code=?",
                            (f.value_at_risk_paise, json.dumps(f.evidence),
                             f.proposed_action, key, code))
                continue
            cur.execute(
                "INSERT OR REPLACE INTO exception (key, code, source, status, "
                "value_at_risk, evidence, proposed_action, opened_run, opened_at) "
                "VALUES (?,?,?, 'open', ?,?,?,?,?)",
                (key, code, f.source, f.value_at_risk_paise,
                 json.dumps(f.evidence), f.proposed_action, run_id, now))
            opened.append((key, code, f.value_at_risk_paise))

        # Anything open last time and absent now has been answered by new data.
        resolved = []
        linked = {m.left for m in res.matches} | {
            r for m in res.matches for r in m.right}
        for (key, code), row in was_open.items():
            if (key, code) in now_present:
                continue
            how = ("matched once the data arrived" if key in linked
                   else "condition no longer present")
            cur.execute("UPDATE exception SET status='resolved', resolved_run=?, "
                        "resolved_at=?, resolution=? WHERE key=? AND code=?",
                        (run_id, now, how, key, code))
            resolved.append((key, code, row["value_at_risk"], how))

        cur.execute("UPDATE run SET new_records=?, opened=?, resolved=?, carried=? "
                    "WHERE id=?",
                    (new_records, len(opened), len(resolved), len(carried), run_id))
        self.db.commit()
        return SyncReport(run_id, len(ds), new_records, opened, resolved,
                          carried, new_links)
