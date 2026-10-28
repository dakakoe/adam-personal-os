"""Pure logic for learn-from-history categorization: memo normalization, the
history index, self-transfer detection, and the per-row classifier."""

from __future__ import annotations

import re

from merge_api import finance_categorize
from merge_api.finance_categorize import (
    norm_memo, is_self_transfer, is_enrichable, looks_like_name,
    build_history_index, classify, merchant_default,
)

# Self-transfer detection is name-driven and configured via FINANCE_SELF_NAMES
# (empty by default → off). Pin a deterministic generic name set for the tests
# so they exercise the matching logic without embedding any real identity.
finance_categorize._SELF_XFER = re.compile(
    r"(smith|m(?:r|ister|istr)\.? *john|mrs?\.? *jane)", re.IGNORECASE)


def test_norm_memo_strips_card_tails_digits_punct():
    a = norm_memo("PROSTOR-SKLAD.RU SANKT-PETERBU RUS")
    b = norm_memo("prostor sklad.ru  sankt-peterbu")
    assert a == b and a  # same merchant → same key, "RUS" noise dropped
    assert norm_memo("Transfer to KBNK x1420 MR SMITH") == norm_memo("transfer to kbnk MR SMITH")
    assert norm_memo(None) == "" and norm_memo("") == ""


def test_self_transfer_detection():
    # own identity → self-transfer
    assert is_self_transfer("Transfer to KBNK x1420 MR. JOHN SMITH")
    assert is_self_transfer("Transfer to BBL x5501 MRS JANE SMITH")
    assert is_self_transfer("รับโอนจาก KBANK x1420 MR JOHN")
    # the bot's slip spellings — "Mister/Mistr John S.", "Top up from John
    # Smith" — must flip from Income to a transfer (the visible bot bug)
    assert is_self_transfer("Transfer from Mister John S.", "to SCB")
    assert is_self_transfer("Top up from John Smith")
    assert is_self_transfer("Mistr John S.")
    # NOT self: generic "received from" a real counterparty (this was the bug —
    # income from Binance was being swallowed as a self-transfer)
    assert not is_self_transfer("รับโอนจาก BAY x7625 GULF BINANCE")
    assert not is_self_transfer("Transfer Withdrawal K PLUS To Pr")
    assert not is_self_transfer("STARBUCKS COFFEE BANGKOK")
    assert not is_self_transfer("Payment to Palm Trade Co", "coffee")
    # NOT self: a bill payment to a merchant (SCB slip FROM=Mr John S. TO=a
    # Bangchak sport-center biller). The payee is the MERCHANT (TO side), so it
    # must stay an expense — the capture passes ONLY the payee here, never the
    # FROM line, so "Mr John S." as the sender no longer flips it to transfer.
    assert not is_self_transfer("บางจาก อนุภาษ ภูเก็ต สปอร์ต เซ็นเตอร์")


def test_merchant_default():
    # messy real SCB/KBank slip strings → clean payee + default category
    assert merchant_default("TOPS-C2B your city Festival") == ("Tops", "Groceries")
    assert merchant_default("Tops your city Floresta") == ("Tops", "Groceries")
    assert merchant_default("BTM(THAILAND)LTD.") == ("Bread Talk", "Groceries")
    assert merchant_default("BREADTALK@CHINATOWN") == ("Bread Talk", "Groceries")
    assert merchant_default("Yamazaki Central your city") == ("Thai Yamazaki", "Groceries")
    assert merchant_default("WWW.GRAB.COM") == ("Grab", "Food Delivery")
    assert merchant_default("Grab( A-4WL72IAWWIP4") == ("Grab", "Food Delivery")
    # taxi carve-out beats the food-delivery default (first match wins)
    assert merchant_default("GRABTAXI (") == ("Grab", "Taxi")
    assert merchant_default("GRAB RIDES-EC") == ("Grab", "Taxi")
    assert merchant_default("เพ็ท คลับ-บูกิส ภูเก็ต") == ("Pet Club", "Pets")
    assert merchant_default("Pet Club Bookis your city") == ("Pet Club", "Pets")
    # Bangchak (a gas-station brand) — matched from the Thai and from the mangled
    # vision transliteration ("Bangkak … School" wrongly became Education)
    assert merchant_default("บางจาก อนุภาษ ภูเก็ต สปอร์ต เซ็นเตอร์") == ("Bangchak", "Gas")
    assert merchant_default("Bangkak Anubanphutket School") == ("Bangchak", "Gas")
    assert merchant_default("Bangchak Anupat your city Sports Center") == ("Bangchak", "Gas")
    # unknown merchants / blanks → no default (left for history + LLM)
    assert merchant_default("STARBUCKS COFFEE BANGKOK") is None
    assert merchant_default("") is None and merchant_default(None) is None


def test_is_enrichable():
    assert is_enrichable("STARBUCKS COFFEE BANGKOK")
    assert is_enrichable("Ч. Светлана Александровна")   # a person's name
    assert not is_enrichable("PMT. PROMPTPAY")           # generic bank op
    assert not is_enrichable("PURCHASE E-CHN")
    assert not is_enrichable("0x9bf2…e9d8")              # wallet address
    assert not is_enrichable("") and not is_enrichable(None)


def test_looks_like_name():
    assert looks_like_name("Aleksandra R.")
    assert looks_like_name("Acme Trading Group")
    assert looks_like_name("Ч. Светлана Александровна")
    assert not looks_like_name("Debit Card Spending - EDC30445")   # has digits
    assert not looks_like_name("จ่ายบิล บางจาก อนุภาษ ภูเก็ต สปอร์ต เซ็นเตอร์")  # long / many words
    assert not looks_like_name(None)


def test_history_index_picks_dominant_category():
    hist = [
        {"category_key": "groceries", "payee_id": None, "payee_text": "Tops Market"},
        {"category_key": "groceries", "payee_id": None, "payee_text": "TOPS market"},
        {"category_key": "cafe", "payee_id": None, "payee_text": "Tops Market"},  # minority
        {"category_key": "cafe", "payee_id": "p1", "payee_text": "Starbucks"},
        {"category_key": "cafe", "payee_id": "p1", "payee_text": "Starbucks"},
    ]
    idx = build_history_index(hist)
    tops = idx["by_memo"][norm_memo("Tops Market")]
    assert tops["category_key"] == "groceries" and tops["count"] == 2  # 2 of 3 → dominant
    assert idx["by_payee_id"]["p1"]["category_key"] == "cafe"


def test_classify_history_then_self_then_none():
    idx = build_history_index([
        {"category_key": "groceries", "payee_id": None, "payee_text": "Tops Market"},
        {"category_key": "groceries", "payee_id": None, "payee_text": "Tops Market"},
    ])
    # exact merchant match (card tail stripped) → history
    d = classify({"payee_text": "TOPS MARKET x1420"}, idx)
    assert d["action"] == "history" and d["category_key"] == "groceries"
    # self-transfer wins even if it looks matchable
    assert classify({"payee_text": "Transfer to KBNK x1420 MR JOHN"}, idx)["action"] == "self_transfer"
    # unknown merchant → left for the LLM pass
    assert classify({"payee_text": "SOME NEW SHOP"}, idx)["action"] == "none"


def test_classify_gates_on_share():
    # a memo split 50/50 across two categories is below the 0.6 confidence gate
    idx = build_history_index([
        {"category_key": "a", "payee_id": None, "payee_text": "Ambiguous Co"},
        {"category_key": "b", "payee_id": None, "payee_text": "Ambiguous Co"},
    ])
    assert classify({"payee_text": "Ambiguous Co"}, idx)["action"] == "none"
