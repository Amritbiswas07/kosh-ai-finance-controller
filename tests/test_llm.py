"""The model layer's parsing and its refusal to leave the candidate set.

No weights are loaded here: `_generate` is replaced, so these run in
milliseconds and pass on a machine with no model cached at all.
"""
import pytest

from kosh.llm import Adjudication, LocalAdjudicator, StubAdjudicator

CANDS = [{"key": "INV-1", "customer": "Anand Traders"},
         {"key": "INV-2", "customer": "Bharat Textiles"},
         {"key": "INV-3", "customer": "Chetna Organics"}]


def _fixed(reply: str) -> LocalAdjudicator:
    adj = LocalAdjudicator()
    adj._generate = lambda system, user: reply          # type: ignore[method-assign]
    return adj


@pytest.mark.parametrize("reply,expected", [
    ("CHOICE: A\nREASON: name matches", "INV-1"),
    ("CHOICE: C\nREASON: closest amount", "INV-3"),
    ("choice: b\nreason: lowercase reply", "INV-2"),
    ("CHOICE - A\nREASON - dash separator", "INV-1"),
])
def test_parses_a_valid_choice(reply, expected):
    assert _fixed(reply).choose("q", CANDS).choice == expected


def test_none_is_not_parsed_as_candidate_n():
    """Regression: `[A-Z]|NONE` matched the N of NONE and read it as option 13."""
    got = _fixed("CHOICE: NONE\nREASON: none of these fit").choose("q", CANDS)
    assert got.choice is None
    assert "out-of-range" not in got.reason
    assert "none of these fit" in got.reason


@pytest.mark.parametrize("reply", [
    "CHOICE: Z\nREASON: past the end of the list",
    "I think it is probably the first one.",
    "",
    "CHOICE:\nREASON: empty",
])
def test_never_returns_something_outside_the_candidate_set(reply):
    assert _fixed(reply).choose("q", CANDS).choice is None


def test_a_named_candidate_is_always_one_that_was_offered():
    keys = {c["key"] for c in CANDS}
    for letter in "ABC":
        got = _fixed(f"CHOICE: {letter}\nREASON: x").choose("q", CANDS)
        assert got.choice in keys


def test_no_candidates_means_no_choice():
    assert _fixed("CHOICE: A\nREASON: x").choose("q", []).choice is None


def test_stub_declines_rather_than_guessing():
    stub = StubAdjudicator()
    assert stub.choose_invoice({}, CANDS)["choice"] is None
    assert stub.choose_payment({}, CANDS)["choice"] is None
    assert stub.choose_bank_line({}, CANDS)["choice"] is None
    assert stub.narrate({}) == ""


def test_confidence_is_never_certain():
    """A model-adjudicated match must never present as fact."""
    got = _fixed("CHOICE: A\nREASON: x").choose("q", CANDS)
    assert 0 < got.confidence < 0.8
    assert Adjudication(None, 0.0, "").as_dict()["choice"] is None
