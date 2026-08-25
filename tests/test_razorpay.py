"""Mapping Razorpay's live recon report onto the engine's own records.

No network and no credentials: the fixture is the documented response shape, so
every unit and edge below is checked the same way on any machine.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from kosh.razorpay import (BASE, MissingCredentials, RazorpayClient, RazorpayError,
                           _redact, to_pg_txn, to_pg_txns, write_csv)

FIXTURE = Path(__file__).parent / "fixtures" / "razorpay_recon.json"


@pytest.fixture(scope="module")
def items():
    return json.loads(FIXTURE.read_text())["items"]


def test_amounts_are_taken_as_paise_not_reparsed_as_rupees(items):
    """The trap this whole module has to avoid: the CSV path carries rupee
    strings and parses them with to_paise, while the API already returns integer
    paise. Sending an API row through the CSV parser multiplies it by 100."""
    pay = to_pg_txn(items[0])
    assert pay.amount_paise == 979200          # ₹9,792.00, exactly as sent
    assert pay.fee_paise == 20000
    assert pay.tax_paise == 3600


def test_direction_comes_from_debit_and_credit_not_the_sign_of_amount(items):
    """The API reports `amount` unsigned, so a refund looks like income unless
    the debit column is read."""
    refund = to_pg_txn(items[1])
    assert items[1]["amount"] > 0              # unsigned in the payload …
    assert refund.amount_paise == -250000      # … and a debit once mapped
    assert to_pg_txn(items[3]).amount_paise == -145000     # chargeback


def test_epoch_timestamps_become_datetimes(items):
    pay = to_pg_txn(items[0])
    assert isinstance(pay.created_at, datetime)
    assert pay.created_at.year == 2026
    assert pay.settled_at is not None and pay.settled_at > pay.created_at


def test_a_held_payment_has_no_settlement(items):
    held = to_pg_txn(items[2])
    assert held.on_hold is True
    assert held.settlement_id is None and held.settled_at is None
    assert held.settled is False


def test_utrs_and_methods_are_normalised(items):
    pay = to_pg_txn(items[0])
    assert pay.settlement_utr == "HDFCN260703551001"      # upper-cased
    assert pay.method == "card"                            # lower-cased
    assert to_pg_txn(items[3]).method == "unknown"         # null method


def test_a_refund_points_at_the_payment_it_reverses(items):
    assert to_pg_txn(items[1]).parent_payment_id == "pay_JVbRT2wjU2X4wy"
    # A payment must not inherit its own id as a parent.
    assert to_pg_txn(items[0]).parent_payment_id is None


def test_a_bad_row_is_reported_not_dropped(items):
    rows, errors = to_pg_txns(items)
    assert len(rows) == 4 and len(errors) == 1
    assert "pay_BROKEN000000" in errors[0]


def test_pulled_rows_round_trip_through_the_normal_parser(items, tmp_path):
    """The live path and the synthetic path must converge before the matcher
    sees either, so a pulled period is written in the same shape and read by the
    same reader."""
    from kosh.ingest import load
    from kosh.generate import build, write

    rows, _ = to_pg_txns(items)
    ds, gt, inj = build(seed=3)
    write(ds, gt, inj, tmp_path, 3)                 # for the other two files
    write_csv(rows, tmp_path / "pg_settlement_report.csv")

    reloaded, errors = load(tmp_path)
    assert not errors
    by_id = {t.entity_id: t for t in reloaded.pg}
    assert by_id["pay_JVbRT2wjU2X4wy"].amount_paise == 979200
    assert by_id["rfnd_JVbSA1pQrS7tZk"].amount_paise == -250000
    assert by_id["pay_JVbSKmNoP4qR6t"].on_hold is True


def test_client_is_read_only():
    source = Path(__file__).resolve().parents[1] / "src/kosh/razorpay.py"
    text = source.read_text()
    assert text.count('method="GET"') == 1
    for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
        assert verb not in text, f"{verb} has no business in a reconciliation client"


def test_credentials_are_scrubbed_from_messages():
    assert _redact("failed for rzp_test_abc:supersecret", "supersecret") \
        == "failed for rzp_test_abc:…redacted…"
    client = RazorpayClient(key_id="rzp_test_abc", key_secret="supersecret")
    assert client.base == BASE
    # The secret is never placed in the URL, only in the Authorization header.
    assert "supersecret" not in f"{client.base}/settlements/recon/combined"


def test_missing_credentials_say_what_to_set(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(MissingCredentials) as e:
        RazorpayClient.from_env()
    assert "RAZORPAY_KEY_ID" in str(e.value) and "rzp_test_" in str(e.value)


def test_paging_follows_skip_until_the_last_short_page(monkeypatch):
    """A month with more rows than one page must not come back truncated."""
    client = RazorpayClient(key_id="k", key_secret="s")
    pages, seen = [[{"entity_id": f"pay_{i}"} for i in range(100)],
                   [{"entity_id": "pay_last"}]], []

    def fake_get(path, params):
        seen.append(params["skip"])
        return {"items": pages[len(seen) - 1] if len(seen) <= len(pages) else []}

    monkeypatch.setattr(client, "_get", fake_get)
    out = client.fetch_recon(2026, 7)
    assert len(out) == 101 and seen == [0, 100]
    assert out[-1]["entity_id"] == "pay_last"


def _row(**over):
    base = {"entity_id": "pay_X", "type": "payment", "debit": 0, "credit": 150000,
            "amount": 150000, "currency": "USD", "fee": 3000, "tax": 540,
            "on_hold": False, "settled": True, "created_at": 1783944600,
            "settled_at": 1784117400, "settlement_id": "setl_x",
            "settlement_utr": "HDFCN1", "order_id": "o1", "order_receipt": "INV-9",
            "method": "card", "dispute_id": None, "payment_id": None}
    return {**base, **over}


def test_a_foreign_currency_row_keeps_its_currency():
    """It used to be refused outright. The minor units of USD and INR happen to
    match, so the figure is the same — but calling it rupees was the bug."""
    txn = to_pg_txn(_row())
    assert txn.currency == "USD" and txn.amount_paise == 150000


def test_a_zero_decimal_currency_survives_the_mapper():
    txn = to_pg_txn(_row(currency="JPY", credit=1570, amount=1570, fee=0, tax=0))
    assert txn.currency == "JPY" and txn.amount_paise == 1570


def test_a_currency_with_unknown_minor_units_is_refused():
    from kosh.schema import CurrencyMismatch
    with pytest.raises(CurrencyMismatch, match="minor-unit"):
        to_pg_txn(_row(currency="XYZ"))
    rows, errors = to_pg_txns([_row(currency="XYZ")])
    assert rows == [] and len(errors) == 1 and "XYZ" in errors[0]


def test_a_missing_currency_is_assumed_to_be_the_base_one(items):
    stripped = {k: v for k, v in items[0].items() if k != "currency"}
    assert to_pg_txn(stripped).amount_paise == 979200


# ------------------------------------------------------------- the request path

def _recording_transport(pages, calls):
    """Stands in for the network, recording what the client actually sent."""
    def send(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        page = pages[min(len(calls) - 1, len(pages) - 1)]
        if isinstance(page, int):
            return page, b'{"error":{"description":"nope"}}'
        return 200, json.dumps(page).encode()
    return send


def test_the_request_carries_basic_auth_and_never_the_secret_in_the_url():
    """The header is the only place a credential belongs."""
    import base64
    calls = []
    c = RazorpayClient(key_id="rzp_test_abc", key_secret="supersecret",
                       transport=_recording_transport([{"items": []}], calls))
    c.fetch_recon(2026, 7, 3)
    sent = calls[0]
    assert sent["url"].startswith("https://api.razorpay.com/v1/settlements/recon/combined?")
    assert "supersecret" not in sent["url"] and "rzp_test_abc" not in sent["url"]
    scheme, token = sent["headers"]["Authorization"].split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "rzp_test_abc:supersecret"
    assert "year=2026" in sent["url"] and "month=07" in sent["url"]
    assert "day=03" in sent["url"]


def test_the_day_is_omitted_when_not_asked_for():
    calls = []
    c = RazorpayClient(key_id="k", key_secret="s",
                       transport=_recording_transport([{"items": []}], calls))
    c.fetch_recon(2026, 7)
    assert "day=" not in calls[0]["url"]


def test_paging_walks_every_page_over_the_real_request_path():
    calls = []
    full = {"items": [{"entity_id": f"pay_{i}"} for i in range(100)]}
    last = {"items": [{"entity_id": "pay_final"}]}
    c = RazorpayClient(key_id="k", key_secret="s",
                       transport=_recording_transport([full, last], calls))
    out = c.fetch_recon(2026, 7)
    assert len(out) == 101
    assert "skip=0" in calls[0]["url"] and "skip=100" in calls[1]["url"]
    assert all("count=100" in c_["url"] for c_ in calls)


@pytest.mark.parametrize("status,expected", [
    (401, "refused the credentials"),
    (403, "refused the credentials"),
    (429, "rate-limiting"),
    (500, "returned 500"),
])
def test_every_error_branch_says_something_useful(status, expected):
    c = RazorpayClient(key_id="rzp_test_abc", key_secret="supersecret",
                       transport=_recording_transport([status], []))
    with pytest.raises(RazorpayError) as e:
        c.fetch_recon(2026, 7)
    assert expected in str(e.value)
    assert "supersecret" not in str(e.value)


def test_a_non_json_body_is_reported_as_such():
    def send(url, headers, timeout):
        return 200, b"<html>maintenance</html>"
    c = RazorpayClient(key_id="k", key_secret="s", transport=send)
    with pytest.raises(RazorpayError, match="not JSON"):
        c.fetch_recon(2026, 7)


def test_an_unreachable_host_is_reported_without_the_secret():
    import urllib.error
    def send(url, headers, timeout):
        raise urllib.error.URLError("nodename nor servname provided")
    c = RazorpayClient(key_id="k", key_secret="supersecret", transport=send)
    with pytest.raises(RazorpayError) as e:
        c.fetch_recon(2026, 7)
    assert "Could not reach Razorpay" in str(e.value)
    assert "supersecret" not in str(e.value)


def test_a_whole_period_maps_end_to_end_over_the_request_path(items):
    """Client, paging, mapping and the engine's own parser, in one pass."""
    calls = []
    c = RazorpayClient(key_id="k", key_secret="s",
                       transport=_recording_transport([{"items": items}], calls))
    rows, errors = to_pg_txns(c.fetch_recon(2026, 7, 3))
    assert len(rows) == 4 and len(errors) == 1
    assert sum(t.amount_paise for t in rows if t.type == "payment") == 979200 + 500000
