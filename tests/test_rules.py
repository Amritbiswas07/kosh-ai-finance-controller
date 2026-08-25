"""Rules a controller states: compiled, inspectable, backtested, revocable.

The model's job here is the furthest it gets from deciding anything. It never
sees a transaction; it reads a sentence and returns fields and thresholds, which
are then checked against a fixed catalogue, shown to the person who asked, and
tested against books whose answers are known before they can affect a close.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from kosh.ingest import build_batches
from kosh.match import Tier, reconcile
from kosh.rules import (Backtest, Condition, Rule, RuleError, backtest,
                        catalogue, explain, matches, parse_compiled)
from kosh.schema import BankLine, Dataset, Invoice


def _inv(no="INV-1", customer="Kaveri Seeds", gross=118000, on=date(2026, 7, 1)):
    return Invoice(no, f"o_{no}", customer, on, gross - 18000, 18000, gross)


def _line(key=1, amount=118000, narration="NEFT-X-KAVERI SEEDS CHENNAI-PMT",
          on=date(2026, 7, 10)):
    return BankLine(key, on, narration, "r", amount, 0)


def _rule(*conditions, name="test-rule", enabled=True):
    r = Rule(name=name, when=list(conditions), author="priya", enabled=enabled)
    r.validate()
    return r


# ------------------------------------------------------------- the catalogue

def test_a_field_outside_the_catalogue_is_refused():
    """The catalogue is the contract: it is what the model is given and what
    validation checks, so nothing else can get in."""
    with pytest.raises(RuleError, match="not something a rule can look at"):
        Condition("bank_account_number", "contains", "x").validate()
    with pytest.raises(RuleError, match="not an operator"):
        Condition("narration", "sounds_like", "x").validate()


def test_an_operator_must_suit_the_field():
    with pytest.raises(RuleError, match="cannot be applied"):
        Condition("narration", "at_most", 5).validate()
    with pytest.raises(RuleError, match="cannot be applied"):
        Condition("amount_gap_pct", "contains", "5").validate()
    with pytest.raises(RuleError, match="needs a number"):
        Condition("amount_gap_pct", "at_most", "roughly five").validate()
    with pytest.raises(RuleError, match="needs some text"):
        Condition("narration", "contains", "  ").validate()


def test_a_rule_with_no_conditions_is_refused():
    """It would match everything, which is not a rule."""
    with pytest.raises(RuleError, match="match everything"):
        Rule(name="empty", when=[]).validate()


def test_a_rule_name_must_be_usable():
    with pytest.raises(RuleError, match="not a usable name"):
        _rule(Condition("narration", "contains", "X"), name="Drop Table; --")


def test_the_catalogue_names_every_field_and_operator():
    text = catalogue()
    for token in ("narration", "amount_gap_pct", "narration_mentions_customer",
                  "contains", "at_most", "is_true"):
        assert token in text


# -------------------------------------------------------------- evaluation

def test_every_condition_must_hold():
    rule = _rule(Condition("narration", "contains", "CHENNAI"),
                 Condition("amount_gap_pct", "at_most", 5))
    assert matches(rule, _inv(), _line())
    assert not matches(rule, _inv(), _line(narration="NEFT-X-MUMBAI-PMT"))
    assert not matches(rule, _inv(), _line(amount=50000))     # gap far over 5%


def test_the_derived_fields_are_computed_not_read():
    rule = _rule(Condition("narration_mentions_customer", "is_true"))
    assert matches(rule, _inv(customer="Kaveri Seeds"), _line())
    assert not matches(rule, _inv(customer="Trident Cables"), _line())


def test_a_percentage_gap_is_relative_to_the_invoice():
    rule = _rule(Condition("amount_gap_pct", "at_most", 2))
    assert matches(rule, _inv(gross=100000), _line(amount=98000))     # exactly 2%
    assert not matches(rule, _inv(gross=100000), _line(amount=97000))  # 3%


def test_not_contains_is_a_real_operator():
    rule = _rule(Condition("narration", "not_contains", "REVERSAL"))
    assert matches(rule, _inv(), _line())
    assert not matches(rule, _inv(), _line(narration="NEFT REVERSAL OF CREDIT"))


def test_matching_is_case_insensitive():
    rule = _rule(Condition("narration", "contains", "chennai"))
    assert matches(rule, _inv(), _line(narration="PMT VIA CHENNAI BRANCH"))


def test_nothing_in_a_rule_is_ever_executed():
    """A rule is data. Operators are looked up by name in a fixed table, so the
    worst a bad compilation can produce is a rule that matches nothing."""
    import kosh.rules as mod
    source = mod.__file__
    text = open(source).read()
    for danger in ("eval(", "exec(", "__import__", "compile("):
        assert danger not in text, f"{danger} has no business in a rule engine"


def test_explain_says_which_condition_failed():
    rule = _rule(Condition("narration", "contains", "MUMBAI"),
                 Condition("amount_gap_pct", "at_most", 5))
    lines = explain(rule, _inv(), _line())
    assert lines[0].startswith("✗") and lines[1].startswith("✓")


# --------------------------------------------------------------- backtesting

def test_a_rule_that_would_mislink_is_visible_before_it_is_enabled():
    """The whole safeguard: a rule is tested against known answers first."""
    invs = [_inv("INV-1", "Kaveri Seeds"), _inv("INV-2", "Trident Cables")]
    lines = [_line(1), _line(2, narration="NEFT-X-TRIDENT CABLES-PMT")]
    truth = {"INV-1": "bank:0001", "INV-2": "bank:0002"}
    good = backtest(_rule(Condition("narration_mentions_customer", "is_true")),
                    invs, lines, truth)
    assert good.precision == 1.0 and len(good.correct) == 2
    assert "all of them correct" in good.verdict()

    sloppy = backtest(_rule(Condition("amount_gap_pct", "at_most", 100)),
                      invs, lines, truth)
    assert sloppy.precision < 1.0
    assert "not real" in sloppy.verdict()


def test_a_rule_that_matches_nothing_says_so():
    bt = backtest(_rule(Condition("narration", "contains", "NOWHERE")),
                  [_inv()], [_line()], {})
    assert bt.proposed == [] and "never fire" in bt.verdict()
    assert bt.precision == 0.0


# ----------------------------------------------------------------- compiling

def test_what_the_model_returns_is_validated_not_trusted():
    ok = parse_compiled('{"name": "x-rule", "when": [{"field": "narration", '
                        '"op": "contains", "value": "ACME"}]}', author="priya")
    assert ok.name == "x-rule" and ok.author == "priya"
    assert ok.enabled is False          # never on by default

    with pytest.raises(RuleError, match="did not return a rule"):
        parse_compiled("I think you want to match on the narration.")
    with pytest.raises(RuleError, match="not valid JSON"):
        parse_compiled('{"name": "bad-json", "when": [oops]}')
    with pytest.raises(RuleError, match="not something a rule can look at"):
        parse_compiled('{"name": "sneaky", "when": [{"field": "secret", '
                       '"op": "contains", "value": "y"}]}')
    # A name that is not a name is refused before anything else is considered.
    with pytest.raises(RuleError, match="not a usable name"):
        parse_compiled('{"name": "x", "when": [{"field": "narration", '
                       '"op": "contains", "value": "y"}]}')


def test_a_compiled_rule_round_trips():
    rule = _rule(Condition("narration", "contains", "ACME"),
                 Condition("amount_gap_pct", "at_most", 2))
    assert Rule.from_json(json.loads(json.dumps(rule.to_json()))).describe() \
        == rule.describe()


# ------------------------------------------------------------- in the engine

def _corpus():
    inv = _inv("INV-1", "Prabhat Printers", 118000)
    line = BankLine(1, date(2026, 7, 8), "RTGS-X-PRABHAT PRNTRS-PMT AGST BILL",
                    "r", 115640, 0)                    # 2% short, name mangled
    return Dataset(invoices=[inv], pg=[], bank=[line])


def test_a_rule_links_what_the_tiers_could_not():
    ds = _corpus()
    without = reconcile(ds, build_batches(ds))
    assert not [m for m in without.matches if m.tier is Tier.LOCAL_RULE]

    rule = _rule(Condition("narration_mentions_customer", "is_true"),
                 Condition("amount_gap_pct", "at_most", 3))
    with_rule = reconcile(ds, build_batches(ds), rules=[rule])
    made = [m for m in with_rule.matches if m.tier is Tier.LOCAL_RULE]
    assert len(made) == 1
    assert made[0].left == "INV-1" and made[0].right == ("bank:0001",)


def test_a_link_made_by_a_rule_names_the_rule_and_its_author():
    """A reviewer should revoke the policy, not hunt the match."""
    ds = _corpus()
    rule = _rule(Condition("narration_mentions_customer", "is_true"),
                 Condition("amount_gap_pct", "at_most", 3))
    m = next(m for m in reconcile(ds, build_batches(ds), rules=[rule]).matches
             if m.tier is Tier.LOCAL_RULE)
    assert m.evidence["rule"] == "test-rule" and m.evidence["author"] == "priya"
    assert "✓" in m.evidence["why"]


def test_a_disabled_rule_does_nothing():
    ds = _corpus()
    rule = _rule(Condition("narration_mentions_customer", "is_true"),
                 enabled=False)
    res = reconcile(ds, build_batches(ds), rules=[rule])
    assert not [m for m in res.matches if m.tier is Tier.LOCAL_RULE]


def test_a_rule_that_cannot_single_one_out_declines():
    """Two candidates satisfy it equally; picking the first would be a guess
    wearing a policy's name."""
    inv = _inv("INV-1", "Prabhat Printers", 118000)
    a = BankLine(1, date(2026, 7, 8), "RTGS-A-PRABHAT PRNTRS-PMT", "r", 115640, 0)
    b = BankLine(2, date(2026, 7, 9), "RTGS-B-PRABHAT PRNTRS-PMT", "r", 115640, 0)
    ds = Dataset(invoices=[inv], pg=[], bank=[a, b])
    rule = _rule(Condition("narration_mentions_customer", "is_true"),
                 Condition("amount_gap_pct", "at_most", 3))
    res = reconcile(ds, build_batches(ds), rules=[rule])
    assert not [m for m in res.matches if m.tier is Tier.LOCAL_RULE]


def test_rules_survive_in_the_ledger(tmp_path):
    from kosh.store import Store
    store = Store(tmp_path / "k.db")
    rule = _rule(Condition("narration", "contains", "ACME"), name="acme")
    store.save_rule(rule, by="amrit")
    assert [r.name for r in store.rules()] == ["acme"]
    assert [r.name for r in store.rules(enabled_only=True)] == ["acme"]
    store.set_rule_enabled("acme", False, by="amrit")
    assert store.rules(enabled_only=True) == []
    assert any(a == "disable_rule" for _at, _who, a, _s, _d in store.history())
    with pytest.raises(KeyError):
        store.set_rule_enabled("nope", True, by="amrit")
    store.close()
