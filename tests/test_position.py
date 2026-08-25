"""The cash bridge must balance, and must not silently plug a difference."""
from kosh.position import build_position, bridge_rows


def test_bridge_identity_holds(run):
    ds, _gt, batches, res = run
    p = build_position(ds, batches, res)
    assert p.settled_net == p.batch_net
    assert p.residual == 0


def test_held_funds_are_excluded_from_settlement(run):
    """The bug this caught: netting fees against captures that never batched."""
    ds, _gt, batches, res = run
    p = build_position(ds, batches, res)
    held = sum(t.amount_paise for t in ds.pg if t.type == "payment" and t.on_hold)
    assert p.on_hold_gross == held > 0
    assert p.captured == p.on_hold_gross + p.unbatched_gross + p.gross_entering_settlement


def test_fees_are_only_charged_on_batched_captures(run):
    ds, _gt, batches, res = run
    p = build_position(ds, batches, res)
    assert p.gateway_fees == sum(t.fee_paise for t in ds.pg
                                 if t.type == "payment" and t.settlement_id)


def test_in_transit_plus_landed_equals_settled(run):
    ds, _gt, batches, res = run
    p = build_position(ds, batches, res)
    assert p.landed_in_bank + p.in_transit == p.batch_net


def test_bridge_rows_render_every_line(run):
    ds, _gt, batches, res = run
    rows = bridge_rows(build_position(ds, batches, res))
    assert len(rows) == 14
    assert all(isinstance(a, int) for _, a, _ in rows)
