"""CLI for the mail content classifier (Phase C).

  python -m mail_ml train    [--sample N] [--per-class N] [--model-dir DIR]
  python -m mail_ml classify [--limit N]  [--model-dir DIR]

`train` builds the SetFit artifact from weak-labeled mail; `classify` (the batch
worker, run by memory-mail-ml.timer) scores unclassified mail into memory.mail_class.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

from . import db

DEFAULT_MODEL_DIR = os.environ.get("MAIL_ML_MODEL_DIR", "/srv/memory/data/mail_ml/model")


async def _train(args) -> None:
    import json

    from . import train as train_mod
    pool = await db.connect()
    try:
        rows = await db.fetch_training_rows(pool, limit=args.sample)
        user_pairs = None
        if args.eval_user:
            user_pairs = train_mod.user_eval_pairs(await db.fetch_user_labels(pool))
    finally:
        await pool.close()
    result = train_mod.train(rows, per_class=args.per_class, model_dir=args.model_dir,
                             user_pairs=user_pairs)
    logging.info("train result: %s", result)
    print(result)
    # Machine-readable metrics for the self-gating retrain script — stdout is a
    # dict REPR (single quotes), not JSON; scripts must read this file instead.
    if args.metrics_json:
        with open(args.metrics_json, "w") as f:
            json.dump(result, f)


async def _classify(args) -> None:
    from . import classify as classify_mod
    clf = classify_mod.Classifier(args.model_dir)     # loads torch/setfit
    pool = await db.connect()
    try:
        rows = await db.fetch_unclassified(pool, limit=args.limit)
        verdicts = classify_mod.classify_rows(clf, rows)
        n = await db.upsert_classes(pool, verdicts)
    finally:
        await pool.close()
    from collections import Counter
    dist = Counter(v["content_class"] for v in verdicts)
    logging.info("classified %d messages: %s", n, dict(dist))
    print({"classified": n, "distribution": dict(dist)})


async def _refine(args) -> None:
    """Local-LLM tier (Phase 5): re-classify low-confidence SetFit verdicts via
    Ollama. Torch-free — safe to run alongside anything."""
    import httpx
    from . import ollama_tier
    pool = await db.connect()
    refined = kept = 0
    try:
        rows = await db.fetch_low_confidence(pool, threshold=args.threshold, limit=args.limit)
        with httpx.Client() as client:
            for r in rows:
                cls = ollama_tier.classify_message(
                    subject=r["subject"], body=r["body_text"], from_address=r["from_address"],
                    url=args.url, model=args.model, client=client)
                version = f"{r['model_version'] or 'setfit'}+ollama"
                if cls is None:
                    # Mark visited (version suffix only — keep SetFit's verdict) so a
                    # dead Ollama / unparseable reply doesn't retry forever.
                    kept += 1
                    await db.mark_refine_visited(
                        pool, r["account_email"], r["message_id"], version)
                    continue
                refined += 1
                await db.upsert_classes(pool, [{
                    "account_email": r["account_email"], "message_id": r["message_id"],
                    "content_class": cls, "confidence": 0.9,
                    "model_version": version}])
    finally:
        await pool.close()
    logging.info("refine: %d refined, %d kept (of %d low-confidence)", refined, kept, refined + kept)
    print({"refined": refined, "kept": kept})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="mail_ml")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train")
    t.add_argument("--sample", type=int, default=8000, help="messages to weak-label")
    t.add_argument("--per-class", type=int, default=200)
    t.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    t.add_argument("--metrics-json", default=None,
                   help="also write the result dict as JSON here (for retrain.sh)")
    t.add_argument("--eval-user", action="store_true",
                   help="report accuracy on user-corrected rows (never gated)")

    c = sub.add_parser("classify")
    c.add_argument("--limit", type=int, default=2000, help="unclassified messages per run")
    c.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)

    r = sub.add_parser("refine")
    r.add_argument("--limit", type=int, default=60, help="low-confidence rows per run")
    r.add_argument("--threshold", type=float,
                   default=float(os.environ.get("MAIL_ML_OLLAMA_THRESHOLD", "0.6")))
    r.add_argument("--url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    r.add_argument("--model", default=(os.environ.get("MAIL_ML_OLLAMA_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen2.5:3b"))

    args = p.parse_args()
    if args.cmd == "train":
        asyncio.run(_train(args))
    elif args.cmd == "refine":
        asyncio.run(_refine(args))
    else:
        asyncio.run(_classify(args))


if __name__ == "__main__":
    main()
