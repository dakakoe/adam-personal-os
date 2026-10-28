"""Train the SetFit content classifier on weak-supervision labels.

Pulls a message sample, weak-labels it (labeling.label_dataset), balances the
classes, fine-tunes SetFit (all-MiniLM-L6-v2), reports held-out accuracy against
the weak labels, and saves the artifact. CPU-only; a few minutes on 4 cores.
"""
from __future__ import annotations

import logging
import random
from collections import Counter

from . import labeling

log = logging.getLogger("mail_ml.train")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# SetFit is few-shot by design — its contrastive step generates O(N²) pairs, so a
# large N blows memory on a small box. With Twenty CRM stopped there's more RAM, so
# we can train on more examples for a better model (undersampling caps the pairs).
DEFAULT_PER_CLASS = 150        # balanced training examples per class
EVAL_FRACTION = 0.25
MAX_STEPS = 1200               # hard cap on embedding-training steps (memory/time)


def _balance(pairs: list[tuple[str, str]], per_class: int, seed: int) -> list[tuple[str, str]]:
    by_class: dict[str, list[tuple[str, str]]] = {c: [] for c in labeling.CLASSES}
    for text, lab in pairs:
        by_class[lab].append((text, lab))
    rng = random.Random(seed)
    out: list[tuple[str, str]] = []
    for c, items in by_class.items():
        rng.shuffle(items)
        out.extend(items[:per_class])
    rng.shuffle(out)
    return out


def user_eval_pairs(user_rows: list[dict]) -> list[tuple[str, str]]:
    """(text, label) pairs from user corrections (mail_class model_version='user'),
    built with the SAME text shape the model trains on. Pure — testable."""
    return [(labeling.build_text(r.get("subject"), r.get("body_text"), r.get("from_address")),
             r["content_class"])
            for r in user_rows
            if r.get("content_class") in labeling.CLASSES]


def train(rows: list[dict], *, per_class: int = DEFAULT_PER_CLASS,
          model_dir: str, seed: int = 13,
          user_pairs: list[tuple[str, str]] | None = None) -> dict:
    """Weak-label `rows`, train SetFit, save to model_dir, return a metrics dict.
    `user_pairs` (user-corrected ground truth) adds a user_accuracy metric —
    reported only, never part of the promote gate (until the corpus is large).
    Imports torch/setfit lazily so importing this module (e.g. in tests) is cheap."""
    from datasets import Dataset
    from setfit import SetFitModel, Trainer, TrainingArguments

    pairs = labeling.label_dataset(rows)
    dist = Counter(lab for _, lab in pairs)
    log.info("weak-labeled %d/%d rows: %s", len(pairs), len(rows), dict(dist))
    if min(dist.get(c, 0) for c in labeling.CLASSES) < 8:
        raise SystemExit(f"too few examples for some class: {dict(dist)}")

    balanced = _balance(pairs, per_class, seed)
    split = int(len(balanced) * (1 - EVAL_FRACTION))
    train_pairs, eval_pairs = balanced[:split], balanced[split:]

    def _ds(pp):
        return Dataset.from_dict({"text": [t for t, _ in pp],
                                  "label": [l for _, l in pp]})

    model = SetFitModel.from_pretrained(MODEL_NAME, labels=list(labeling.CLASSES))
    # Small batch + capped steps + few contrastive iterations keep peak RSS well
    # under the box's headroom (shared with Twenty/Postgres/Rspamd).
    args = TrainingArguments(
        batch_size=8, num_epochs=1, num_iterations=10,
        max_steps=MAX_STEPS, sampling_strategy="undersampling", seed=seed)
    trainer = Trainer(model=model, args=args,
                      train_dataset=_ds(train_pairs), eval_dataset=_ds(eval_pairs))
    trainer.train()
    metrics = trainer.evaluate()          # {'accuracy': ...} vs the weak labels
    model.save_pretrained(model_dir)
    log.info("saved model → %s (held-out acc %.3f)", model_dir, metrics.get("accuracy", 0.0))

    result = {"trained": len(train_pairs), "eval": len(eval_pairs),
              "distribution": dict(dist), "metrics": metrics}
    if user_pairs:
        preds = model.predict([t for t, _ in user_pairs])
        hits = sum(1 for p, (_, want) in zip(preds, user_pairs) if str(p) == want)
        result["user_accuracy"] = hits / len(user_pairs)
        result["user_n"] = len(user_pairs)
        log.info("user-corrections eval: %.3f over %d", result["user_accuracy"], len(user_pairs))
    return result
