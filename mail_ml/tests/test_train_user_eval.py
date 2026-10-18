"""User-corrections eval pairs (triage iteration) — pure, no torch import."""
from __future__ import annotations

from mail_ml import labeling as L
from mail_ml.train import _balance, user_eval_pairs


def test_user_eval_pairs_shape_matches_training_text() -> None:
    rows = [{"content_class": "newsletter", "subject": "Weekly digest",
             "body_text": "News inside", "from_address": "News <news@shop.com>"}]
    pairs = user_eval_pairs(rows)
    assert len(pairs) == 1
    text, label = pairs[0]
    assert label == "newsletter"
    assert text == L.build_text("Weekly digest", "News inside", "News <news@shop.com>")


def test_user_eval_pairs_drops_unknown_classes() -> None:
    rows = [{"content_class": "spam", "subject": "x", "body_text": "y", "from_address": "z"},
            {"content_class": None, "subject": "x", "body_text": "y", "from_address": "z"},
            {"content_class": "personal", "subject": "x", "body_text": "y", "from_address": "z"}]
    assert [lab for _, lab in user_eval_pairs(rows)] == ["personal"]


def test_balance_is_deterministic() -> None:
    pairs = [(f"t{i}", cls) for i in range(30) for cls in L.CLASSES]
    a = _balance(pairs, per_class=5, seed=13)
    b = _balance(pairs, per_class=5, seed=13)
    assert a == b
    assert len(a) == 5 * len(L.CLASSES)
