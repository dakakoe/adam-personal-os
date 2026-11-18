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


def apply_guards(cls: str, conf: float, row: dict, body: str) -> tuple[str, float, str]:
    """Hard rules on top of the model, which won't obey them reliably. Only a
    `personal` verdict is ever changed, and only ever away from personal:

    - a role/no-reply sender is a machine → transactional;
    - list mail (List-Unsubscribe / List-Id) → newsletter, or transactional for
      a plain receipt;
    - a stranger at volume (labeling.is_stranger_at_volume) → transactional,
      or newsletter when it reads as marketing; tagged SENDER_TAG.

    Returns (class, confidence, model_version suffix)."""
    if cls != labeling.PERSONAL:
        return cls, conf, ""
    if labeling._is_role_sender(row.get("from_address")):
        return labeling.TRANSACTIONAL, 1.0, ""      # machines aren't people
    if labeling.has_list_header(row.get("headers")):
        return labeling.list_verdict(row.get("subject"), body), 1.0, ""  # lists aren't people
    if labeling.is_stranger_at_volume(row.get("sender_is_own"), row.get("sender_known"),
                                      row.get("sender_sends")):
        return labeling.bulk_verdict(row.get("subject"), body), 1.0, labeling.SENDER_TAG
    return cls, conf, ""


def classify_rows(clf: Classifier, rows: list[dict]) -> list[dict]:
    """rows: [{account_email, message_id, subject, body_text, body_html,
    from_address, headers, sender_is_own, sender_known, sender_sends}] →
    verdict dicts. The model reads the plain-text body, or the HTML's visible
    text when there is none; apply_guards runs on its answer."""
    if not rows:
        return []
    bodies = [labeling.body_for_model(r.get("body_text"), r.get("body_html")) for r in rows]
    texts = [labeling.build_text(r.get("subject"), b, r.get("from_address"))
             for r, b in zip(rows, bodies)]
    preds = clf.predict(texts)
    out = []
    for r, body, (cls, conf) in zip(rows, bodies, preds):
        cls, conf, tag = apply_guards(cls, conf, r, body)
        out.append({"account_email": r["account_email"], "message_id": r["message_id"],
                    "content_class": cls, "confidence": round(conf, 4),
                    "model_version": clf.version + tag})
    return out


def reguard_rows(rows: list[dict]) -> list[dict]:
    """Re-check existing model verdicts of `personal` against the guards, with
    no model call. A verdict outgrows its guards when a rule is added or a
    stranger's tenth email arrives — both happened to thousands of rows scored
    before the rule existed. Returns verdicts only for rows that change."""
    out = []
    for r in rows:
        body = labeling.body_for_model(r.get("body_text"), r.get("body_html"))
        cls, conf, tag = apply_guards(labeling.PERSONAL, float(r.get("confidence") or 0.0), r, body)
        if cls == labeling.PERSONAL:
            continue
        out.append({"account_email": r["account_email"], "message_id": r["message_id"],
                    "content_class": cls, "confidence": conf,
                    "model_version": (r.get("model_version") or "setfit") + tag})
    return out
