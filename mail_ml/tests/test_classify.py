"""classify_rows — what the model reads and the hard guards on its verdict.

Runs without torch: the SetFit model is replaced by a stub that records what it
was shown and answers whatever the test says it would."""
from __future__ import annotations

from mail_ml import labeling as L
from mail_ml.classify import classify_rows


class _Stub:
    version = "stub"

    def __init__(self, answer=L.PERSONAL, conf=0.8):
        self.answer, self.conf, self.seen = answer, conf, []

    def predict(self, texts):
        self.seen.extend(texts)
        return [(self.answer, self.conf)] * len(texts)


def _row(**kw):
    base = {"account_email": "me@example.com", "message_id": "m1", "subject": "Hello",
            "body_text": "", "body_html": None, "from_address": "dana@example.org",
            "headers": {}}
    return {**base, **kw}


def test_html_only_mail_is_read_through_its_visible_text():
    clf = _Stub()
    html = ("<html><head><style>.x{color:red}</style></head><body>"
            "<p>Big summer&nbsp;sale</p><script>track()</script></body></html>")
    classify_rows(clf, [_row(body_html=html)])
    seen = clf.seen[0]
    assert "Big summer sale" in seen
    assert "<p>" not in seen and "color:red" not in seen and "track()" not in seen


def test_plain_text_wins_over_html():
    clf = _Stub()
    classify_rows(clf, [_row(body_text="the real text", body_html="<p>html copy</p>")])
    assert "the real text" in clf.seen[0] and "html copy" not in clf.seen[0]


def test_list_mail_is_never_personal():
    out = classify_rows(_Stub(), [_row(headers={"list-unsubscribe": "<mailto:u@x.com>"},
                                       body_text="Our weekly digest")])
    assert out[0]["content_class"] == L.NEWSLETTER and out[0]["confidence"] == 1.0


def test_list_receipt_becomes_transactional_not_newsletter():
    out = classify_rows(_Stub(), [_row(headers={"list-id": "<shop.example>"},
                                       subject="Your order #123 receipt", body_text="Paid.")])
    assert out[0]["content_class"] == L.TRANSACTIONAL


def test_list_guard_only_overrides_personal():
    out = classify_rows(_Stub(answer=L.TRANSACTIONAL, conf=0.7),
                        [_row(headers={"list-unsubscribe": "<x>"})])
    assert out[0]["content_class"] == L.TRANSACTIONAL and out[0]["confidence"] == 0.7


def test_person_without_list_header_stays_personal():
    out = classify_rows(_Stub(), [_row(body_text="Lunch next week?")])
    assert out[0]["content_class"] == L.PERSONAL and out[0]["confidence"] == 0.8


def test_role_sender_guard_still_wins():
    out = classify_rows(_Stub(), [_row(from_address="no-reply@bank.example")])
    assert out[0]["content_class"] == L.TRANSACTIONAL


def test_subject_only_mail_is_still_classified():
    clf = _Stub()
    out = classify_rows(clf, [_row(body_text=None, body_html=None, subject="Quick question")])
    assert "Quick question" in clf.seen[0] and len(out) == 1


def test_empty_batch():
    assert classify_rows(_Stub(), []) == []


# ---- stranger rule + reguard -------------------------------------------------

from mail_ml.classify import apply_guards, reguard_rows  # noqa: E402

STRANGER = {"sender_is_own": False, "sender_known": False, "sender_sends": L.STRANGER_MIN_SENDS}


def test_stranger_at_volume_is_never_personal_and_is_tagged():
    out = classify_rows(_Stub(), [_row(**STRANGER, body_text="Build failed on main")])
    assert out[0]["content_class"] == L.TRANSACTIONAL
    assert out[0]["model_version"] == "stub" + L.SENDER_TAG


def test_stranger_marketing_reads_as_newsletter():
    cls, _, tag = apply_guards(L.PERSONAL, 0.9, _row(**STRANGER), "50% off, shop now")
    assert cls == L.NEWSLETTER and tag == L.SENDER_TAG


def test_below_threshold_a_stranger_stays_personal():
    row = _row(**{**STRANGER, "sender_sends": L.STRANGER_MIN_SENDS - 1})
    assert classify_rows(_Stub(), [row])[0]["content_class"] == L.PERSONAL


def test_people_you_know_and_your_own_mail_are_never_caught():
    many = L.STRANGER_MIN_SENDS * 10
    known = _row(sender_is_own=False, sender_known=True, sender_sends=many)
    own = _row(sender_is_own=True, sender_known=False, sender_sends=many)
    out = classify_rows(_Stub(), [known, own])
    assert [o["content_class"] for o in out] == [L.PERSONAL, L.PERSONAL]
    assert all(o["model_version"] == "stub" for o in out)


def test_rows_without_sender_stats_are_not_strangers():
    assert apply_guards(L.PERSONAL, 0.8, _row(), "hi")[0] == L.PERSONAL


def test_role_and_list_guards_win_over_the_stranger_tag():
    role = apply_guards(L.PERSONAL, 0.8, _row(**STRANGER, from_address="no-reply@x.example"), "")
    listed = apply_guards(L.PERSONAL, 0.8, _row(**STRANGER, headers={"list-id": "<x>"}), "")
    assert role[2] == "" and listed[2] == ""


def _verdict(**kw):
    base = {"account_email": "me@example.com", "message_id": "m1", "confidence": 0.7,
            "model_version": "setfit-v3", "subject": "Hi", "body_text": "hello there",
            "body_html": None, "from_address": "dana@example.org", "headers": {},
            "sender_is_own": False, "sender_known": False, "sender_sends": 1}
    return {**base, **kw}


def test_reguard_returns_only_rows_that_change():
    rows = [_verdict(message_id="keep"),
            _verdict(message_id="list", headers={"list-unsubscribe": "<x>"}),
            _verdict(message_id="stranger", sender_sends=L.STRANGER_MIN_SENDS),
            _verdict(message_id="role", from_address="comments-noreply@docs.example")]
    out = {v["message_id"]: v for v in reguard_rows(rows)}
    assert set(out) == {"list", "stranger", "role"}
    assert out["stranger"]["model_version"] == "setfit-v3" + L.SENDER_TAG
    assert out["list"]["model_version"] == "setfit-v3"
    assert all(v["confidence"] == 1.0 for v in out.values())


def test_reguard_reads_html_only_mail_for_the_keyword_check():
    row = _verdict(sender_sends=L.STRANGER_MIN_SENDS, body_text="",
                   body_html="<p>Flash sale — 40% off</p>")
    assert reguard_rows([row])[0]["content_class"] == L.NEWSLETTER
