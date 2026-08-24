"""Reading identifiers out of narrations a bank actually emits."""
import pytest
from kosh.normalize import (extract_utrs, looks_like_settlement, norm_id,
                            norm_name, token_overlap)


@pytest.mark.parametrize("narration,utr", [
    ("ACH/C/SBIN0260703589071/RAZORPAY SOFTWARE", "SBIN0260703589071"),
    ("mb:neft cr-icicn260704675657-razorpay-settlement", "ICICN260704675657"),
    ("NEFT CR HDFCN260712345678 RAZORPAY  PVT  LTD", "HDFCN260712345678"),
    ("RTGS KKBKN260706745359 RAZORPAY SOFTWARE PRIVATE LIMITED", "KKBKN260706745359"),
])
def test_extracts_utr_through_formatting(narration, utr):
    assert extract_utrs(narration) == [utr]


@pytest.mark.parametrize("narration", [
    "SALARY PAYOUT AUG BATCH 1", "CHQ PAID-004512", "", "RENT-UNIT 402 SKYVIEW-MAR",
])
def test_finds_nothing_where_there_is_nothing(narration):
    assert extract_utrs(narration) == []


def test_interest_credit_digits_are_not_a_utr():
    # 'INT.PD:12345678:...' has a digit run but no bank prefix.
    assert extract_utrs("INT.PD:12345678:01-08-2026 TO 31-08-2026") == []


def test_settlement_gate_keeps_business_traffic_out():
    assert looks_like_settlement("NEFT-HDFCN260712345678-RAZORPAY-SETTLEMENT")
    assert looks_like_settlement("neft cr razorpaysoft consolidated payout")
    # A real-looking UTR on a credit that is plainly not gateway money.
    assert not looks_like_settlement("NEFT-CITIN25081200099-ANAND TRADERS-DIRECT")
    assert not looks_like_settlement("TERM LOAN DISBURSAL TL-99321")


def test_identifier_normalisation():
    assert norm_id("INV-2627-1001") == norm_id("inv26271001") == "inv26271001"
    assert norm_id(None) == ""


def test_company_suffixes_do_not_count_as_evidence():
    assert norm_name("Anand Traders Pvt Ltd") == "anand traders"
    assert token_overlap("Anand Traders Pvt Ltd", "ANAND TRADERS") == 1.0
    assert token_overlap("Anand Traders", "Bharat Textiles") == 0.0
    assert token_overlap("", "anything") == 0.0
