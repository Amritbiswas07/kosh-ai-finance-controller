"""Three days of a close, to show the difference state makes.

Day 1  Some settlements have left the gateway but the money has not landed —
       normal at T+2. They open as exceptions.
Day 2  The bank statement now carries those credits. Nothing about the engine
       changed; the data caught up. The open breaks clear themselves, and the
       ledger records that they did.
Day 3  The same file is loaded again by mistake. Nothing moves.

That last day is the quiet one, and it is the reason fingerprints exist: a
reconciliation you can safely re-run is a reconciliation somebody can automate.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kosh.generate import build, write                    # noqa: E402
from kosh.ingest import build_batches, load               # noqa: E402
from kosh.match import reconcile                          # noqa: E402
from kosh.money import fmt                                # noqa: E402
from kosh.schema import Dataset                           # noqa: E402
from kosh.store import Store                              # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "outputs"
HOLD_BACK = 4          # credits that have not reached the bank on day 1


def show(day: str, rep) -> None:
    print(f"\n─── {day} " + "─" * (58 - len(day)))
    print(f"  {rep.records:,} records, {rep.new_records:,} new or amended, "
          f"{rep.new_links} new links")
    for k, c, v in sorted(rep.opened, key=lambda x: -abs(x[2]))[:4]:
        print(f"    opened   {k:<20} {c:<26} {fmt(v):>13}")
    if len(rep.opened) > 4:
        print(f"    …and {len(rep.opened) - 4} more opened")
    for k, c, v, how in sorted(rep.resolved, key=lambda x: -abs(x[2])):
        print(f"    CLEARED  {k:<20} {c:<26} {fmt(v):>13}  ({how})")
    if not rep.opened and not rep.resolved:
        print("    nothing changed")
    if rep.carried:
        oldest = max(a for *_, a in rep.carried)
        print(f"    {len(rep.carried)} still open, oldest {oldest} run(s) old")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    db = OUT / "live_demo.db"
    if db.exists():
        db.unlink()
    store = Store(db)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        full, gt, inj = build(seed=20260824)
        write(full, gt, inj, root, 20260824)
        full_ds, errors = load(root)
        assert not errors, errors

        # Day 1: hold back the most recent credits — the money is in flight.
        settled = sorted(full_ds.bank, key=lambda b: b.value_date)
        held = {b.key for b in [b for b in settled if b.is_credit][-HOLD_BACK:]}
        day1 = Dataset(invoices=list(full_ds.invoices), pg=list(full_ds.pg),
                       bank=[b for b in full_ds.bank if b.key not in held])
        r1 = reconcile(day1, build_batches(day1))
        show("Day 1 · settlements sent, money still in flight", store.sync(day1, r1))

        # Day 2: the statement catches up. Same engine, more data.
        r2 = reconcile(full_ds, build_batches(full_ds))
        show("Day 2 · the bank statement catches up", store.sync(full_ds, r2))

        # Day 3: somebody loads the same export again.
        r3 = reconcile(full_ds, build_batches(full_ds))
        show("Day 3 · the same file is loaded twice", store.sync(full_ds, r3))

    print("\n─── ledger " + "─" * 53)
    for k, v in store.counts().items():
        print(f"  {k:<22} {v:>6,}")
    store.close()
    print(f"\nstate kept in {db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
