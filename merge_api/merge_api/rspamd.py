"""Rspamd spam scoring for the mail client (Phase B2).

Self-hosted Rspamd (Apache 2.0) runs as a docker service on the droplet; its
normal worker exposes an HTTP /checkv2 endpoint that scans a raw RFC822 message
and returns {score, action, symbols}. merge_api POSTs the raw message (fetched
from Gmail with format=raw) and persists the verdict in memory.mail_spam.

No auth on the scan endpoint — it's bound to 127.0.0.1 on the host.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

# Rspamd actions that mean "this is spam-ish", most→least severe.
SPAMMY_ACTIONS = {"reject", "add header", "rewrite subject", "soft reject"}


class RspamdError(RuntimeError):
    """Rspamd is unreachable or returned an unusable response."""


def _parse(body: dict) -> dict:
    """Normalize a /checkv2 response to {score, action, symbols}. `symbols` is
    flattened to {name: score} to keep the stored jsonb small and explainable."""
    action = (body.get("action") or "no action").strip().lower()
    score = body.get("score")
    symbols_raw = body.get("symbols") or {}
    symbols = {}
    for name, sym in symbols_raw.items():
        if isinstance(sym, dict):
            symbols[name] = sym.get("score")
        else:
            symbols[name] = sym
    return {
        "score": float(score) if score is not None else None,
        "action": action,
        "symbols": symbols,
    }


async def check(raw_message: bytes, *, url: str, client: httpx.AsyncClient | None = None) -> dict:
    """Scan one raw RFC822 message. Returns {score, action, symbols}. Raises
    RspamdError on transport/HTTP/parse failure (the caller skips that message —
    a scan miss must never wedge the batch)."""
    endpoint = f"{url.rstrip('/')}/checkv2"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.post(endpoint, content=raw_message,
                                 headers={"Content-Type": "application/octet-stream"})
        resp.raise_for_status()
        return _parse(resp.json())
    except Exception as e:  # noqa: BLE001
        raise RspamdError(str(e)) from e
    finally:
        if owns:
            await client.aclose()


def is_spammy(action: str | None) -> bool:
    return (action or "").strip().lower() in SPAMMY_ACTIONS


# --- Bayes training (controller worker, /learnspam + /learnham) ---------------

def parse_learn_result(status_code: int, body: dict | None) -> str:
    """Normalize a /learn* response → 'learned' | 'already'. Rspamd reports an
    already-trained message either as HTTP 208 or as a 200/400 with an
    'already learned' error text; both are success for our ledger (the message
    is in the statistics). Anything else raises RspamdError."""
    # 208 = already in the statistics; 204 = learning skipped by rspamd (learn
    # condition / duplicate hash) — both terminal: retrying can never succeed,
    # so record them in the ledger rather than re-feeding every run.
    if status_code in (204, 208):
        return "already"
    body = body or {}
    if status_code == 200 and body.get("success"):
        return "learned"
    err = str(body.get("error") or "").lower()
    if "already learned" in err:
        return "already"
    raise RspamdError(f"learn failed: HTTP {status_code} {body}")


async def learn(kind: str, raw_message: bytes, *, url: str,
                client: httpx.AsyncClient | None = None) -> str:
    """Feed one raw RFC822 message to Bayes. kind: 'spam' | 'ham'. Returns
    'learned' or 'already'; raises RspamdError otherwise (caller skips that
    message — a learn miss must never wedge the batch)."""
    assert kind in ("spam", "ham")
    endpoint = f"{url.rstrip('/')}/learn{kind}"
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        resp = await client.post(endpoint, content=raw_message,
                                 headers={"Content-Type": "application/octet-stream"})
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None
        return parse_learn_result(resp.status_code, body)
    except RspamdError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RspamdError(str(e)) from e
    finally:
        if owns:
            await client.aclose()


def plan_learn_batch(spam_available: int, ham_available: int, *, limit: int,
                     learned_spam: int, learned_ham: int,
                     max_ratio: float = 3.0) -> tuple[int, int]:
    """How many spam/ham to learn this run. Aims for an even split of `limit`,
    gives unused slack to the class that still has candidates, and caps the
    CUMULATIVE class imbalance at max_ratio (min_learns is per-class — a lopsided
    corpus delays BAYES_* firing and skews the classifier)."""
    half = limit // 2
    n_spam = min(spam_available, half)
    n_ham = min(ham_available, half)
    # hand leftover budget to whichever side still has candidates
    slack = limit - n_spam - n_ham
    if slack > 0:
        extra_spam = min(slack, spam_available - n_spam)
        n_spam += extra_spam
        slack -= extra_spam
    if slack > 0:
        n_ham += min(slack, ham_available - n_ham)
    # cumulative imbalance cap: don't let one class run away
    if n_spam and (learned_ham + n_ham) and \
            (learned_spam + n_spam) / max(1, learned_ham + n_ham) > max_ratio:
        n_spam = max(0, int(max_ratio * (learned_ham + n_ham)) - learned_spam)
    if n_ham and (learned_spam + n_spam) and \
            (learned_ham + n_ham) / max(1, learned_spam + n_spam) > max_ratio:
        n_ham = max(0, int(max_ratio * (learned_spam + n_spam)) - learned_ham)
    return n_spam, n_ham
