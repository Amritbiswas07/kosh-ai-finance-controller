"""Q&A must be grounded in the run, and must decline when it has nothing."""
from kosh.ask import answer, retrieve
from kosh.position import build_position


def _ctx(run):
    ds, _gt, batches, res = run
    return res, build_position(ds, batches, res)


def test_retrieval_finds_a_named_record(run):
    res, pos = _ctx(run)
    key = next(f.key for f in res.unresolved())
    facts = retrieve(f"why did {key} not reconcile?", res, pos)
    assert any(key in f for f in facts)


def test_cash_questions_pull_the_position(run):
    res, pos = _ctx(run)
    facts = retrieve("how much cash is still in transit?", res, pos)
    assert any(f.startswith("[POSITION]") for f in facts)


def test_declines_when_nothing_is_relevant(run):
    res, pos = _ctx(run)
    out = answer("what is the airspeed velocity of an unladen swallow?", res, pos)
    assert out["facts"] == []
    assert "Nothing in this reconciliation" in out["answer"]


def test_without_a_model_it_reports_records_rather_than_prose(run):
    res, pos = _ctx(run)
    out = answer("which settlements are missing from the bank?", res, pos)
    assert out["model_used"] is False and out["grounded"] is True
    assert out["facts"]


def test_invented_amounts_are_caught(run):
    """The model must not compute a total that is not already in the evidence."""
    from kosh.ask import answer, check_numbers
    facts = ["[POSITION] still in transit 71,950.38, on hold 8,033.51"]
    assert check_numbers("In transit is 71,950.38.", facts) == set()
    assert check_numbers("The total is 79,983.89.", facts) == {"79983.89"}
    # A year or a bare count is not an amount and must not trip the check.
    assert check_numbers("There are 4 items dated 2026.", facts) == set()


def test_a_fabricating_model_is_withheld_not_printed(run):
    res, pos = _ctx(run)

    class Fabricator:
        def _generate(self, system, user):
            return "The combined exposure is 99,999.99 across these items."

    out = answer("how much is still in transit?", res, pos, Fabricator())
    assert out["numeric_check"] == "failed"
    assert "99999.99" in out["invented_amounts"]
    assert "99,999.99" not in out["answer"].split("records are:")[0].replace(
        "(99999.99)", "")


def test_a_grounded_model_answer_passes(run):
    res, pos = _ctx(run)
    key = next(f.key for f in res.unresolved())

    class Quoter:
        def _generate(self, system, user):
            return f"{key} has not reconciled."

    out = answer(f"what happened to {key}?", res, pos, Quoter())
    assert out["numeric_check"] == "passed"
    assert key in out["answer"]


def test_aggregation_language_is_rejected(run):
    """Numbers can be real and the claim still wrong."""
    res, pos = _ctx(run)

    class Aggregator:
        def _generate(self, system, user):
            return "Funds on hold totalling $8,033.51 remain unsettled."

    out = answer("what is on hold?", res, pos, Aggregator())
    assert out["numeric_check"] == "failed"
    assert out["rejected_phrase"] is not None
    assert out["rejected_phrase"].lower() in {"totalling", "$"}
