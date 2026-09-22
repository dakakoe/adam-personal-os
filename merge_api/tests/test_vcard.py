"""vcard.py — parsing an iPhone contacts export (pure, no DB).

All fixtures are invented: example.com addresses and 555 numbers only."""
from __future__ import annotations

from datetime import date

from merge_api.vcard import (normalize_phone, parse_international, parse_vcards, raw_digits,
                             suggest_fix, summarize)


def _parse(text: str, cc: str | None = None):
    return parse_vcards(text.splitlines(keepends=True), default_country_code=cc)


IPHONE_CARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "PRODID:-//Apple Inc.//iPhone OS 17.5//EN\r\n"
    "N:Lee;Dana;;;\r\n"
    "FN:Dana Lee\r\n"
    "ORG:Acme Tokens;Research\r\n"
    "TITLE:Head of Partnerships\r\n"
    "item1.EMAIL;type=INTERNET;type=pref:Dana@Example.com\r\n"
    "item1.X-ABLabel:_$!<Other>!$_\r\n"
    "TEL;type=CELL;type=VOICE;type=pref:+1 (201) 555-0100\r\n"
    "NOTE:Met at the Seoul summit\\nIntro via Sam\\, CTO\r\n"
    "BDAY;X-APPLE-OMIT-YEAR=1604:1604-05-01\r\n"
    "item2.URL;type=pref:https://example.com/dana\r\n"
    "PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRgABAQAAAQABAAD\r\n"
    " 2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0\r\n"
    " Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIy\r\n"
    "UID:4B2E6D3A-1111-2222-3333-444455556666\r\n"
    "END:VCARD\r\n"
)


def test_parses_an_iphone_card():
    [c] = _parse(IPHONE_CARD)
    assert c.uid == "4B2E6D3A-1111-2222-3333-444455556666"
    assert (c.display_name, c.given_name, c.family_name) == ("Dana Lee", "Dana", "Lee")
    assert (c.organization, c.job_title) == ("Acme Tokens", "Head of Partnerships")
    assert c.emails == ["dana@example.com"]
    assert c.phones_raw == ["+1 (201) 555-0100"] and c.phones == ["12015550100"]
    assert c.notes == "Met at the Seoul summit\nIntro via Sam, CTO"
    assert c.urls == ["https://example.com/dana"]


def test_photo_data_never_leaks_into_other_fields():
    [c] = _parse(IPHONE_CARD)
    blob = repr(c)
    assert "9j/4AAQ" not in blob and "2wBDAAgGB" not in blob and "Hyc5PTgy" not in blob


def test_year_less_birthday_is_flagged_not_dated_1604():
    [c] = _parse(IPHONE_CARD)
    assert c.birthday == date(1604, 5, 1) and c.birthday_has_year is False


def test_birthday_formats():
    def bday(line):
        return _parse(f"BEGIN:VCARD\nFN:X\n{line}\nEND:VCARD\n")[0]
    full = bday("BDAY:1990-05-01")
    assert full.birthday == date(1990, 5, 1) and full.birthday_has_year
    assert bday("BDAY:19900501").birthday == date(1990, 5, 1)
    assert bday("BDAY:1990-05-01T00:00:00Z").birthday == date(1990, 5, 1)
    v4 = bday("BDAY:--0501")
    assert v4.birthday == date(1604, 5, 1) and v4.birthday_has_year is False
    assert bday("BDAY:not a date").birthday is None


def test_folded_lines_are_joined():
    [c] = _parse("BEGIN:VCARD\nFN:Dana\nNOTE:first half \n and second half\nEND:VCARD\n")
    assert c.notes == "first half and second half"


def test_phone_normalization():
    assert normalize_phone("+44 20 7946 0958") == "442079460958"
    assert normalize_phone("0044 20 7946 0958") == "442079460958"
    assert normalize_phone("+1 201-555-0100 ext. 12") == "12015550100"
    assert normalize_phone("+1 201-555-0100,123") == "12015550100"
    # A national number is only placed when a country code is configured.
    assert normalize_phone("081 234 5678") is None
    assert normalize_phone("081 234 5678", default_country_code="66") == "66812345678"
    # Too short to be a real international number.
    assert normalize_phone("+1 555") is None
    assert normalize_phone("") is None and normalize_phone(None) is None


def test_country_code_setting_is_sanitised():
    [c] = _parse("BEGIN:VCARD\nFN:Local\nTEL:081 234 5678\nEND:VCARD\n", cc="+66")
    assert c.phones == ["66812345678"]


def test_emails_and_numbers_are_deduplicated():
    card = ("BEGIN:VCARD\nFN:Dup\nEMAIL:a@example.com\nEMAIL:A@Example.com\n"
            "TEL:+1 201 555 0100\nTEL:+1 (201) 555-0100\nEND:VCARD\n")
    [c] = _parse(card)
    assert c.emails == ["a@example.com"]
    assert c.phones == ["12015550100"] and len(c.phones_raw) == 2


def test_vcard_21_quoted_printable_and_bare_types():
    card = ("BEGIN:VCARD\nVERSION:2.1\n"
            "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=D0=94=D0=B0=D0=BD=\n"
            "=D0=B0\n"
            "TEL;CELL;PREF:+7 999 555 01 00\nEND:VCARD\n")
    [c] = _parse(card)
    assert c.display_name == "Дана"
    assert c.phones == ["79995550100"]


def test_display_name_falls_back_sensibly():
    [by_n] = _parse("BEGIN:VCARD\nN:Lee;Dana;;;\nEND:VCARD\n")
    [by_org] = _parse("BEGIN:VCARD\nORG:Acme Plumbing\nEND:VCARD\n")
    [by_tel] = _parse("BEGIN:VCARD\nTEL:+1 201 555 0100\nEND:VCARD\n")
    assert (by_n.display_name, by_org.display_name, by_tel.display_name) == \
        ("Dana Lee", "Acme Plumbing", "+1 201 555 0100")


def test_card_without_uid_gets_a_stable_one():
    card = "BEGIN:VCARD\nFN:No Uid\nTEL:+1 201 555 0100\nEND:VCARD\n"
    [a] = _parse(card)
    [b] = _parse(card)
    assert a.uid.startswith("hash:") and a.uid == b.uid


def test_content_hash_changes_only_when_the_card_does():
    [a] = _parse(IPHONE_CARD)
    [b] = _parse(IPHONE_CARD)
    [c] = _parse(IPHONE_CARD.replace("Head of Partnerships", "CEO"))
    assert a.content_hash == b.content_hash != c.content_hash


def test_several_cards_and_junk_between_them():
    text = IPHONE_CARD + "garbage line\n" + "BEGIN:VCARD\nFN:Second\nEND:VCARD\n" + "BEGIN:VCARD\nFN:Unterminated\n"
    assert [c.display_name for c in _parse(text)] == ["Dana Lee", "Second"]


def test_summary_counts_numbers_that_cannot_be_matched():
    contacts = _parse("BEGIN:VCARD\nFN:A\nTEL:+1 201 555 0100\nTEL:081 234 5678\nEND:VCARD\n"
                      "BEGIN:VCARD\nFN:B\nEND:VCARD\n")
    assert summarize(contacts) == {"with_phone": 1, "numbers": 2, "numbers_without_country": 1}


def test_raw_digits_is_the_fix_key():
    assert raw_digits("8 (916) 555-01-00") == raw_digits("89165550100") == "89165550100"
    assert raw_digits("8 916 555 01 00 ext. 12") == "89165550100"
    assert raw_digits(None) == ""


def test_parse_international_requires_the_plus():
    assert parse_international("+7 916 555-01-00") == "79165550100"
    assert parse_international("79165550100") is None
    assert parse_international("+7 555") is None
    assert parse_international(None) is None


def test_suggestions_only_for_unambiguous_shapes():
    assert suggest_fix("79165550100") == {
        "action": "set", "international": "79165550100", "label": "Russian numbers written without +"}
    assert suggest_fix("89165550100")["international"] == "79165550100"
    assert suggest_fix("12015550100")["international"] == "12015550100"
    assert suggest_fix("66812345678")["international"] == "66812345678"
    assert suggest_fix("5550100") == {"action": "ignore", "label": "Too short to be a phone number"}
    # Could be Russian or Indian: no guess.
    assert suggest_fix("9165550100") is None
    # A local trunk-0 number needs the default country to be suggested.
    assert suggest_fix("0812345678") is None
    assert suggest_fix("0812345678", "66")["international"] == "66812345678"
    assert suggest_fix("") is None
