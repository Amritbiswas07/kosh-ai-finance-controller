"""The corpus must be reproducible, and its ground truth internally consistent."""
from kosh.generate import Injections, _abbreviate, build
from kosh.schema import ExceptionCode


def test_same_seed_same_world():
    a, ga, _ = build(seed=99)
    b, gb, _ = build(seed=99)
    assert a.counts() == b.counts()
    assert ga.exceptions == gb.exceptions
    assert [i.invoice_no for i in a.invoices] == [i.invoice_no for i in b.invoices]


def test_different_seeds_differ():
    a, _, _ = build(seed=1)
    b, _, _ = build(seed=2)
    assert [i.gross_paise for i in a.invoices] != [i.gross_paise for i in b.invoices]


def test_corpus_clears_the_track_minimum():
    ds, _, _ = build(seed=11)
    assert len(ds) > 50            # Track 4 asks for 50+ records
    assert len(ds.invoices) and len(ds.pg) and len(ds.bank)


def test_every_exception_code_is_exercised():
    """Across both corpora. A few codes exist only for cases the main generator
    is not supposed to contain — that is the adversarial corpus's job."""
    from kosh.adversary import build as build_adversarial
    from kosh.ingest import build_batches as _batches
    from kosh.match import reconcile as _reconcile

    _ds, gt, _ = build(seed=20260824)
    present = set(gt.exceptions.values())
    ads, _cases = build_adversarial()
    present |= {f.code.value for f in _reconcile(ads, _batches(ads)).findings}
    expected = ({c.value for c in ExceptionCode}
                - {ExceptionCode.UNCLASSIFIED.value}
                # Exercised by the multi-currency corpus; see test_fx.py.
                - {ExceptionCode.FX_REVALUATION.value,
                   ExceptionCode.FX_RATE_MISSING.value,
                   ExceptionCode.MIXED_CURRENCY_BATCH.value})
    assert expected - present == set(), f"never exercised: {expected - present}"


def test_ground_truth_keys_exist_in_the_data(run):
    ds, gt, _batches, _res = run
    keys = ({i.invoice_no for i in ds.invoices} | {t.entity_id for t in ds.pg}
            | {b.key for b in ds.bank} | {t.settlement_id for t in ds.pg if t.settlement_id})
    unknown = set(gt.exceptions) - keys
    assert not unknown, f"ground truth flags records that do not exist: {unknown}"


def test_a_duplicate_capture_always_follows_its_original(run):
    """Otherwise 'which one is the duplicate' is not decidable from the data."""
    ds, gt, _b, _r = run
    by_id = {t.entity_id: t for t in ds.pg}
    for key, code in gt.exceptions.items():
        if code != ExceptionCode.DUPLICATE_PAYMENT.value:
            continue
        dup = by_id[key]
        siblings = [t for t in ds.pg if t.type == "payment"
                    and t.order_id == dup.order_id and t.entity_id != key]
        assert siblings
        assert all(dup.created_at > s.created_at for s in siblings)


def test_jitter_varies_difficulty():
    import random
    counts = {Injections.jittered(random.Random(s)).total() for s in range(30)}
    assert len(counts) > 10


def test_abbreviator_keeps_the_first_word():
    assert _abbreviate("Bharat Textiles") == "BHARAT TXTLS"
    assert _abbreviate("Anand Traders") == "ANAND TRDRS"


def test_bank_generated_credits_share_no_customer_tokens():
    """The guard on Leg D rests on this: a bank's own credit never names a customer."""
    from kosh.normalize import token_overlap
    ds, _, _ = build(seed=13)
    customers = {i.customer for i in ds.invoices}
    for line in ds.bank:
        if not line.narration.upper().startswith(("INT.PD", "TERM LOAN", "NACH", "CHQ")):
            continue
        assert all(token_overlap(c, line.narration) == 0 for c in customers), line.narration
