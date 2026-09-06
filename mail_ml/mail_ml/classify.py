"""Batch inference: load the trained SetFit model and classify messages that have
no verdict yet, writing {content_class, confidence} to memory.mail_class."""
from __future__ import annotations

import logging
import os

from . import labeling

log = logging.getLogger("mail_ml.classify")


class Classifier:
    """Lazily loads the SetFit artifact once, then predicts a class + confidence."""

    def __init__(self, model_dir: str):
        from setfit import SetFitModel
        self.model = SetFitModel.from_pretrained(model_dir)
        self.version = os.path.basename(model_dir.rstrip("/")) or "setfit"

    def predict(self, texts: list[str]) -> list[tuple[str, float]]:
        # predict_proba → per-class probabilities; take argmax + its probability.
        probs = self.model.predict_proba(texts)
        labels = list(self.model.labels or labeling.CLASSES)
        out: list[tuple[str, float]] = []
        for row in probs:
            vals = [float(x) for x in row]
            best = max(range(len(vals)), key=lambda i: vals[i])
            out.append((labels[best], vals[best]))
        return out


def classify_rows(clf: Classifier, rows: list[dict]) -> list[dict]:
    """rows: [{account_email, message_id, subject, body_text, from_address}] →
    verdict dicts. A deterministic guard runs on top of the model: a role/no-reply
    sender is never `personal` (a learned model won't obey that hard rule reliably,
    so we enforce it) — such a prediction is corrected to transactional."""
    if not rows:
        return []
    texts = [labeling.build_text(r.get("subject"), r.get("body_text"), r.get("from_address")) for r in rows]
    preds = clf.predict(texts)
    out = []
    for r, (cls, conf) in zip(rows, preds):
        if cls == labeling.PERSONAL and labeling._is_role_sender(r.get("from_address")):
            cls, conf = labeling.TRANSACTIONAL, 1.0    # hard rule: machines aren't people
        out.append({"account_email": r["account_email"], "message_id": r["message_id"],
                    "content_class": cls, "confidence": round(conf, 4),
                    "model_version": clf.version})
    return out
