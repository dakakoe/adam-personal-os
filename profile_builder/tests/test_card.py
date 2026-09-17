"""compose_card — the one-line role card find_people ranks by.

Pure (no torch), so it runs anywhere:
    uv run --no-project --with pytest python -m pytest tests/test_card.py
"""
from __future__ import annotations

from profile_builder.card import MAX_LEAD, compose_card, lead_sentence


def test_takes_only_the_lead_sentence_of_a_written_profile():
    summary = "Dana Lee is a partner at a crypto fund. You met in Bangkok. She replied fast."
    assert compose_card("Dana Lee", summary) == "Dana Lee. Dana Lee is a partner at a crypto fund."


def test_bio_summary_does_not_repeat_the_name_as_its_lead():
    assert compose_card("Dana Lee", "Dana Lee. VP Sales · Acme. Based in Berlin.") == \
        "Dana Lee. VP Sales · Acme."


def test_appends_roles_the_lead_does_not_already_say():
    card = compose_card("Dana Lee", "Dana Lee runs payments.", "VP Sales at Acme", "Advisor · Beta")
    assert card == "Dana Lee. Dana Lee runs payments. VP Sales at Acme. Advisor · Beta."


def test_skips_a_role_already_in_the_lead_case_insensitively():
    card = compose_card("Dana Lee", "Dana Lee is VP Sales at Acme.", "vp sales at acme")
    assert card == "Dana Lee. Dana Lee is VP Sales at Acme."


def test_role_with_trailing_period_gets_one_period():
    assert compose_card("Dana", None, "Founder.") == "Dana. Founder."


def test_nothing_but_a_name_is_no_card():
    assert compose_card("Dana Lee", None) is None
    assert compose_card("Dana Lee", "Dana Lee.") is None
    assert compose_card("Dana Lee", "   ", "", None) is None


def test_long_unbroken_lead_is_capped_on_a_word():
    lead = lead_sentence("word " * 200)
    assert len(lead) <= MAX_LEAD + 1 and lead.endswith("…") and "  " not in lead


def test_is_deterministic_so_unchanged_inputs_never_reembed():
    args = ("Dana", "Dana builds wallets. More.", "CTO at Acme", "CTO · Acme")
    assert compose_card(*args) == compose_card(*args)
