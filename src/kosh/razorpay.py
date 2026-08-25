"""A read-only client for Razorpay's settlement recon report.

Everything else in Kosh reads a CSV that was written to look like this report.
This reads the report itself, from
`GET /v1/settlements/recon/combined?year=&month=&day=`, and maps it onto the
same `PGTxn` the rest of the engine already understands — so the live path and
the synthetic path converge before the matcher sees either.

**Read-only by construction.** There is one request method, it issues `GET`, and
nothing in this module can create, modify or move money. That is a deliberate
property of a tool that is pointed at a production payments account.

**Credentials are never handled here beyond reading them.** They come from the
environment, are passed to `urllib`'s Basic auth, and are scrubbed from every
error message this module raises — an API error that echoes the request URL is
the ordinary way a key ends up in a log file.

Three details of the real API differ from the CSV, and each is the kind that
silently produces wrong money:

  *amounts are already integer paise* — the CSV carries rupee strings, so the
  synthetic path parses with `to_paise` and this path must not, or every figure
  comes out a hundred times too large
  *timestamps are Unix epochs*, not ISO strings
  *direction lives in `debit`/`credit`*, not in the sign of `amount`
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .currency import CurrencyError, exponent
from .schema import BASE_CURRENCY, CurrencyMismatch, PGTxn

BASE = "https://api.razorpay.com/v1"
RECON_PATH = "/settlements/recon/combined"
PAGE = 100          # the endpoint accepts 1–1000; smaller pages fail faster


class RazorpayError(RuntimeError):
    pass


class MissingCredentials(RazorpayError):
    pass


def _redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "…redacted…")
    return text


def _urllib_transport(url: str, headers: dict, timeout: int) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@dataclass
class RazorpayClient:
    """Reads settlement recon rows. Issues GET and nothing else.

    `transport` exists so the request path — the Authorization header, paging,
    and every error branch — can be exercised without a live account. Only the
    default one touches a socket; everything above it is the same code either
    way, which is the part worth testing.
    """

    key_id: str
    key_secret: str
    base: str = BASE
    timeout: int = 30
    transport: object = None

    @classmethod
    def from_env(cls) -> "RazorpayClient":
        key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        if not key_id or not secret:
            raise MissingCredentials(
                "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in the environment. "
                "Use test-mode keys (they begin rzp_test_); this client only ever "
                "issues GET, but there is no reason to point it at live keys.")
        return cls(key_id=key_id, key_secret=secret)

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base}{path}?" + urllib.parse.urlencode(params)
        token = base64.b64encode(
            f"{self.key_id}:{self.key_secret}".encode()).decode()
        headers = {"Authorization": f"Basic {token}",
                   "Accept": "application/json",
                   "User-Agent": "kosh-reconciler/1.0 (read-only)"}
        send = self.transport or _urllib_transport
        try:
            status, body = send(url, headers, self.timeout)
        except urllib.error.URLError as e:
            raise RazorpayError(
                f"Could not reach Razorpay: {_redact(str(e.reason), self.key_secret)}"
            ) from None
        if status in (401, 403):
            raise RazorpayError(
                f"Razorpay refused the credentials ({status}). Check "
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.") from None
        if status == 429:
            raise RazorpayError(
                "Razorpay is rate-limiting this key (429). Retry with a smaller "
                "period, or wait before pulling again.") from None
        if status >= 400:
            text = _redact(body.decode(errors="replace")[:400],
                           self.key_secret, self.key_id)
            raise RazorpayError(f"Razorpay returned {status}: {text}") from None
        try:
            return json.loads(body.decode())
        except json.JSONDecodeError as e:
            raise RazorpayError(
                f"Razorpay returned {status} with a body that is not JSON: {e}"
            ) from None

    def fetch_recon(self, year: int, month: int, day: int | None = None,
                    limit: int | None = None) -> list[dict]:
        """Every recon row for a period, following `skip`/`count` to the end."""
        params: dict = {"year": year, "month": f"{month:02d}"}
        if day is not None:
            params["day"] = f"{day:02d}"
        out: list[dict] = []
        skip = 0
        while True:
            page = self._get(RECON_PATH, {**params, "count": PAGE, "skip": skip})
            items = page.get("items") or []
            out.extend(items)
            if len(items) < PAGE or (limit and len(out) >= limit):
                break
            skip += PAGE
        return out[:limit] if limit else out


# --------------------------------------------------------------------- mapping

def _epoch(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def to_pg_txn(item: dict) -> PGTxn:
    """One recon row as the engine's own record.

    `amount` arrives as integer paise and is used as-is. The synthetic CSV
    carries rupee strings and is parsed with `to_paise`; running this row
    through that would multiply every figure by a hundred.
    """
    currency = (item.get("currency") or BASE_CURRENCY).strip().upper()
    try:
        exponent(currency)          # minor units must be known, not assumed
    except CurrencyError:
        raise CurrencyMismatch(str(item.get("entity_id", "?")), currency) from None

    kind = (item.get("type") or "").lower()
    credit = int(item.get("credit") or 0)
    debit = int(item.get("debit") or 0)
    # Direction comes from the debit/credit columns, not the sign of `amount`,
    # which the API reports unsigned.
    signed = credit - debit if (credit or debit) else int(item.get("amount") or 0)

    created = _epoch(item.get("created_at"))
    if created is None:
        raise RazorpayError(f"recon row {item.get('entity_id')} has no created_at")

    return PGTxn(
        entity_id=str(item["entity_id"]),
        type=kind or "payment",
        amount_paise=signed,
        fee_paise=int(item.get("fee") or 0),
        tax_paise=int(item.get("tax") or 0),
        created_at=created,
        method=(item.get("method") or "unknown").lower(),
        order_id=item.get("order_id") or None,
        order_receipt=item.get("order_receipt") or None,
        settlement_id=item.get("settlement_id") or None,
        settlement_utr=(item.get("settlement_utr") or "").upper() or None,
        settled_at=_epoch(item.get("settled_at")),
        on_hold=bool(item.get("on_hold")),
        dispute_id=item.get("dispute_id") or None,
        # For a refund the report points at the payment it reverses.
        parent_payment_id=(item.get("payment_id")
                           if kind == "refund" else None) or None,
        currency=currency,
    )


def to_pg_txns(items: list[dict]) -> tuple[list[PGTxn], list[str]]:
    """Map a whole page, collecting failures rather than dropping them."""
    rows, errors = [], []
    for item in items:
        try:
            rows.append(to_pg_txn(item))
        except (RazorpayError, KeyError, ValueError, TypeError) as exc:
            errors.append(f"{item.get('entity_id', '<no id>')}: {exc}")
    return rows, errors


def write_csv(rows: list[PGTxn], path) -> None:
    """Write the engine's own settlement-report shape, so a pulled period and a
    generated one are read by exactly the same parser."""
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_id", "type", "debit", "credit", "amount", "currency",
                    "fee", "tax", "on_hold", "settled", "created_at", "settled_at",
                    "settlement_id", "settlement_utr", "order_id", "order_receipt",
                    "method", "dispute_id", "parent_payment_id"])
        for t in sorted(rows, key=lambda x: x.created_at):
            w.writerow([
                t.entity_id, t.type,
                f"{-t.amount_paise / 100:.2f}" if t.amount_paise < 0 else "0.00",
                f"{t.amount_paise / 100:.2f}" if t.amount_paise > 0 else "0.00",
                f"{t.amount_paise / 100:.2f}", t.currency,
                f"{t.fee_paise / 100:.2f}", f"{t.tax_paise / 100:.2f}",
                "Y" if t.on_hold else "N", "Y" if t.settled else "N",
                t.created_at.isoformat(sep=" "),
                t.settled_at.isoformat(sep=" ") if t.settled_at else "",
                t.settlement_id or "", t.settlement_utr or "", t.order_id or "",
                t.order_receipt or "", t.method, t.dispute_id or "",
                t.parent_payment_id or ""])
