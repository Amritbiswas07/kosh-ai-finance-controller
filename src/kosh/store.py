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

from .match import Disposition, ReconResult
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
  assignee TEXT, note TEXT,
  resolved_by TEXT, approved_by TEXT, approved_at TEXT,
  PRIMARY KEY (key, code)
);
-- A link a person confirmed by hand. Replayed into every later run so the
-- same question is never asked twice, and kept append-only so the decision
-- and who made it survive.
CREATE TABLE IF NOT EXISTS manual_link (
  leg TEXT NOT NULL, left_key TEXT NOT NULL, right_key TEXT NOT NULL,
  confirmed_by TEXT NOT NULL, note TEXT, confirmed_at TEXT NOT NULL,
  PRIMARY KEY (leg, left_key, right_key)
);
-- A rule a controller stated, as compiled and as backtested. Kept whether or
-- not it is enabled, so a rejected one is a record of what was tried.
CREATE TABLE IF NOT EXISTS rule (
  name TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  author TEXT, source_text TEXT,
  enabled INTEGER NOT NULL DEFAULT 0,
  backtest TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  subject TEXT NOT NULL, detail TEXT
);
"""

#: Statuses an exception moves through. `written_off` is a decision, not a
#: disappearance — the money is still gone, somebody chose to stop chasing it.
STATUSES = ("open", "investigating", "resolved", "written_off")
#: Above this, a resolution needs a second person. Self-approving a large
#: write-off is the control this exists to prevent.
APPROVAL_THRESHOLD_PAISE = 10_000_00


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
    vanished: list[tuple[str, str, int]]         # open, but its record left the data
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
            "vanished": [{"key": k, "code": c, "value": str(to_rupees(v))}
                         for k, c, v in self.vanished],
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
            "SELECT * FROM exception WHERE status IN ('open','investigating')"
        ).fetchall()
        return {(r["key"], r["code"]): r for r in rows}

    def counts(self) -> dict:
        q = lambda s: self.db.execute(s).fetchone()[0]     # noqa: E731
        return {"runs": q("SELECT COUNT(*) FROM run"),
                "records": q("SELECT COUNT(*) FROM record"),
                "links": q("SELECT COUNT(*) FROM link"),
                "open_exceptions": q(
                    "SELECT COUNT(*) FROM exception "
                    "WHERE status IN ('open','investigating')"),
                "assigned": q(
                    "SELECT COUNT(*) FROM exception WHERE assignee IS NOT NULL "
                    "AND status IN ('open','investigating')"),
                "confirmed_links": q("SELECT COUNT(*) FROM manual_link"),
                "rules_enabled": q("SELECT COUNT(*) FROM rule WHERE enabled = 1"),
                "resolved_exceptions": q(
                    "SELECT COUNT(*) FROM exception "
                    "WHERE status IN ('resolved','written_off')")}

    def manual_links(self) -> set[tuple[str, str, str]]:
        """Confirmations to replay into the next reconciliation."""
        return {(r[0], r[1], r[2]) for r in self.db.execute(
            "SELECT leg, left_key, right_key FROM manual_link")}

    def rules(self, enabled_only: bool = False) -> list:
        from .rules import Rule
        sql = "SELECT body FROM rule" + (" WHERE enabled = 1" if enabled_only else "")
        return [Rule.from_json(json.loads(r[0])) for r in self.db.execute(sql)]

    def save_rule(self, rule, by: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO rule VALUES (?,?,?,?,?,?,?)",
            (rule.name, json.dumps(rule.to_json()), rule.author, rule.source_text,
             1 if rule.enabled else 0, json.dumps(rule.backtest),
             datetime.now().isoformat(timespec="seconds")))
        self._audit(by, "enable_rule" if rule.enabled else "save_rule", rule.name,
                    rule.source_text[:180])
        self.db.commit()

    def set_rule_enabled(self, name: str, on: bool, by: str) -> None:
        from .rules import Rule
        row = self.db.execute("SELECT body FROM rule WHERE name = ?",
                              (name,)).fetchone()
        if row is None:
            raise KeyError(f"no rule called {name!r}")
        rule = Rule.from_json(json.loads(row[0]))
        rule.enabled = on
        self.db.execute("UPDATE rule SET enabled = ?, body = ? WHERE name = ?",
                        (1 if on else 0, json.dumps(rule.to_json()), name))
        self._audit(by, "enable_rule" if on else "disable_rule", name, "")
        self.db.commit()

    def history(self, limit: int = 20) -> list[tuple]:
        return self.db.execute(
            "SELECT at, actor, action, subject, detail FROM audit "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---------------------------------------------------------------- actions
    def _audit(self, actor: str, action: str, subject: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO audit (at, actor, action, subject, detail) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), actor, action, subject, detail))

    def _row(self, key: str, code: str):
        self.db.row_factory = sqlite3.Row
        row = self.db.execute(
            "SELECT * FROM exception WHERE key=? AND code=?", (key, code)).fetchone()
        if row is None:
            raise KeyError(f"no exception {key}/{code}")
        return row

    def assign(self, key: str, code: str, to: str, by: str) -> None:
        self._row(key, code)
        self.db.execute("UPDATE exception SET assignee=?, status=CASE WHEN "
                        "status='open' THEN 'investigating' ELSE status END "
                        "WHERE key=? AND code=?", (to, key, code))
        self._audit(by, "assign", f"{key}/{code}", f"to {to}")
        self.db.commit()

    def annotate(self, key: str, code: str, note: str, by: str) -> None:
        self._row(key, code)
        self.db.execute("UPDATE exception SET note=? WHERE key=? AND code=?",
                        (note, key, code))
        self._audit(by, "note", f"{key}/{code}", note[:200])
        self.db.commit()

    def resolve(self, key: str, code: str, by: str, note: str,
                status: str = "resolved", approved_by: str | None = None) -> None:
        """Close a break by decision rather than by the data changing.

        A resolution above the threshold needs a second name, and it cannot be
        the same name: a control that one person can satisfy alone is not a
        control. Below it, one person suffices — requiring two signatures for a
        ₹23 bank charge is how controls get routed around.
        """
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        row = self._row(key, code)
        exposure = abs(row["value_at_risk"] or 0)
        if exposure >= APPROVAL_THRESHOLD_PAISE:
            if not approved_by:
                raise PermissionError(
                    f"{key}/{code} carries {to_rupees(exposure)}, at or above the "
                    f"{to_rupees(APPROVAL_THRESHOLD_PAISE)} threshold, so it needs "
                    "a second approver.")
            if approved_by.strip().lower() == by.strip().lower():
                raise PermissionError(
                    f"{by} cannot approve their own resolution of {key}/{code}.")
        now = datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "UPDATE exception SET status=?, resolution=?, resolved_by=?, "
            "approved_by=?, approved_at=?, resolved_at=?, resolved_run=("
            "SELECT MAX(id) FROM run) WHERE key=? AND code=?",
            (status, note, by, approved_by, now if approved_by else None,
             now, key, code))
        self._audit(by, status, f"{key}/{code}",
                    f"{note}" + (f" · approved by {approved_by}" if approved_by else ""))
        self.db.commit()

    def confirm_link(self, leg: str, left: str, right: str, by: str,
                     note: str = "") -> None:
        """Record that a person matched two records the engine could not."""
        self.db.execute(
            "INSERT OR REPLACE INTO manual_link VALUES (?,?,?,?,?,?)",
            (leg, left, right, by, note,
             datetime.now().isoformat(timespec="seconds")))
        self._audit(by, "confirm_link", f"{left} -> {right}", f"{leg} · {note}")
        self.db.commit()

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
        decided = {(r[0], r[1]) for r in self.db.execute(
            "SELECT key, code FROM exception WHERE status IN ('resolved','written_off')")}
        now_present = {(f.key, f.code.value): f for f in res.findings
                       if f.disposition is Disposition.NEEDS_REVIEW}

        opened, carried = [], []
        for (key, code), f in now_present.items():
            if (key, code) in decided:
                continue      # a person closed this; the run does not reopen it
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

        # An open break that is absent from this run has either been answered or
        # had its record disappear — and those are not the same thing. A
        # truncated export used to clear breaks silently: the settlement had not
        # arrived, it had simply stopped being in the file, and the ledger
        # recorded the money as reconciled. So absence only counts as a
        # resolution when the record it concerns is still in front of us.
        present_keys = (
            {i.invoice_no for i in ds.invoices}
            | {t.entity_id for t in ds.pg}
            | {t.settlement_id for t in ds.pg if t.settlement_id}
            | {b.key for b in ds.bank})
        resolved, vanished = [], []
        linked = {m.left for m in res.matches} | {
            r for m in res.matches for r in m.right}
        for (key, code), row in was_open.items():
            if (key, code) in now_present:
                continue
            if key not in present_keys:
                vanished.append((key, code, row["value_at_risk"]))
                cur.execute(
                    "UPDATE exception SET resolution=? WHERE key=? AND code=?",
                    ("record absent from the latest data; still open", key, code))
                continue
            how = ("matched once the data arrived" if key in linked
                   else "condition no longer present")
            cur.execute("UPDATE exception SET status='resolved', resolved_run=?, "
                        "resolved_at=?, resolution=? WHERE key=? AND code=?",
                        (run_id, now, how, key, code))
            resolved.append((key, code, row["value_at_risk"], how))

        cur.execute("UPDATE run SET new_records=?, opened=?, resolved=?, carried=? "
                    "WHERE id=?",
                    (new_records, len(opened), len(resolved),
                     len(carried) + len(vanished), run_id))
        self.db.commit()
        return SyncReport(run_id, len(ds), new_records, opened, resolved,
                          carried, vanished, new_links)
