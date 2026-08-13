"""Weak-supervision labeling for the mail content classifier (Phase C).

The Tier-1 signals we already have — Gmail's own category labels plus the Phase B
header heuristics and a few keyword rules — ARE the labeling functions (Snorkel
style). They produce noisy labels for `newsletter | transactional | personal`,
which train the SetFit model; the model then generalizes to mail the rules abstain
on. Pure + dependency-free so it's unit-testable without torch.
"""
from __future__ import annotations

import re

NEWSLETTER = "newsletter"
TRANSACTIONAL = "transactional"
PERSONAL = "personal"
CLASSES = (NEWSLETTER, TRANSACTIONAL, PERSONAL)

# Content keywords. Transactional = system/account/commerce events aimed at ONE
# recipient; newsletter = broadcast marketing/editorial. Kept deliberately tight
# (high precision) — recall comes from the trained model, not these rules.
_TXN_KW = re.compile(
    r"\b(receipt|invoice|order\s+#|your\s+order|confirmation|confirm\s+your|"
    r"verify|verification|one[-\s]?time|otp|password|reset|shipped|shipping|"
    r"delivery|delivered|payment|paid|refund|booking|reservation|itinerary|"
    r"boarding|ticket|statement|balance|due\s+date|appointment)\b", re.I)
_NEWS_KW = re.compile(
    r"(unsubscribe|newsletter|weekly\s+digest|this\s+week|new\s+arrivals?|"
    r"\d+%\s*off|\bsale\b|\bdeals?\b|discount|promo|limited\s+time|shop\s+now|"
    r"black\s+friday|cyber\s+monday|flash\s+sale)", re.I)


def _header_signals(headers: dict | None) -> tuple[bool, bool]:
    """(has_list, automated) from the persisted triage headers (may be empty for
    mail ingested before Phase B)."""
    h = headers or {}
    has_list = bool((h.get("list-unsubscribe") or "").strip()
                    or (h.get("list-id") or "").strip())
    auto = (h.get("auto-submitted") or "").strip().lower()
    return has_list, bool(auto) and auto != "no"


# A "role"/machine sender is never a real person → never `personal`. Matches
# no-reply-style local-parts and ESP sending sub-domains (email.apple.com, mail.x.com).
# Marketing role senders (newsletters@, marketing@) still land as `newsletter`
# because they carry List-Unsubscribe and are caught by the has_list check first.
_ROLE_LOCALPART = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|noreply|notifications?|notify|"
    r"alerts?|billing|invoices?|receipts?|payments?|orders?|support|helpdesk|help|"
    r"info|hello|team|sales|marketing|newsletters?|news|updates?|mailer|bounce[sd]?|"
    r"postmaster|mail(er)?[-_.]?daemon|automated|system|accounts?|security|service|"
    r"contact|feedback|welcome|reply|email|mail)([-_.+].*)?$", re.I)
_ESP_DOMAIN = re.compile(
    r"@(email|mail|mailer|news|newsletter|notifications?|updates?|reply|noreply|"
    r"bounce|mktg|marketing|sendgrid|mailgun)\.", re.I)


def _is_role_sender(from_address: str | None) -> bool:
    """True when the sender is a machine/role address (no human behind it)."""
    a = (from_address or "").strip().lower()
    if "@" not in a:
        return False
    local = a.split("@", 1)[0]
    return bool(_ROLE_LOCALPART.match(local) or _ESP_DOMAIN.search(a))


def weak_label(*, labels, subject: str | None, body: str | None,
               headers: dict | None = None, from_address: str | None = None) -> str | None:
    """A weak label for one message, or None to ABSTAIN (dropped from training).

    Header signals come FIRST now that they're backfilled: List-Unsubscribe/List-Id
    is a strong subscription/newsletter marker (this is what fixes 'subscriptions
    labelled personal'), Auto-Submitted marks system/transactional mail. Gmail's
    category is only the fallback for mail with no persisted headers."""
    labs = set(labels or [])
    text = f"{subject or ''}\n{body or ''}"
    has_list, automated = _header_signals(headers)
    news_kw = bool(_NEWS_KW.search(text))
    txn_kw = bool(_TXN_KW.search(text))

    # --- 1) Strong header signals (highest precision) ----------------------
    if has_list:
        # A bulk/list sender → newsletter, unless it's plainly a transactional list
        # message (receipt/order) with no marketing language.
        return TRANSACTIONAL if (txn_kw and not news_kw) else NEWSLETTER
    if automated:
        # Auto-Submitted with no list header → a one-to-one system notification.
        return TRANSACTIONAL

    # --- 2) Gmail category backbone (mail with no persisted headers) --------
    base: str | None = None
    if "CATEGORY_PROMOTIONS" in labs:
        base = NEWSLETTER
    elif "CATEGORY_UPDATES" in labs:
        base = TRANSACTIONAL
    elif "CATEGORY_SOCIAL" in labs:
        base = TRANSACTIONAL
    elif "CATEGORY_FORUMS" in labs:
        base = NEWSLETTER
    elif "CATEGORY_PERSONAL" in labs:
        base = PERSONAL

    role = _is_role_sender(from_address)
    if base is not None:
        # 'Personal' means a human wrote it — a role/no-reply sender Gmail happened
        # to file under Personal (billing@, no_reply@email.apple.com) is transactional.
        if base == PERSONAL and role:
            return TRANSACTIONAL
        if base == NEWSLETTER and txn_kw and not news_kw:
            return None
        if base == TRANSACTIONAL and news_kw and not txn_kw:
            return None
        return base

    # --- 3) Sender + keyword fallback (no header, no Gmail category) --------
    if role:
        return TRANSACTIONAL      # machine sender with no list header → system/txn
    if news_kw:
        return NEWSLETTER
    if txn_kw:
        return TRANSACTIONAL
    return PERSONAL               # a human address, no automation signals


def label_dataset(rows) -> list[tuple[str, str]]:
    """Map DB rows (labels/subject/body/headers/from_address) → (text, label)
    pairs, dropping abstentions."""
    out: list[tuple[str, str]] = []
    for r in rows:
        lab = weak_label(labels=r.get("labels"), subject=r.get("subject"),
                         body=r.get("body_text"), headers=r.get("headers"),
                         from_address=r.get("from_address"))
        if lab is None:
            continue
        out.append((build_text(r.get("subject"), r.get("body_text"), r.get("from_address")), lab))
    return out


def build_text(subject: str | None, body: str | None, from_address: str | None = None,
               *, body_chars: int = 1000) -> str:
    """The text the model sees: sender + subject (weighted) + a body snippet. The
    sender address/domain is highly predictive (newsletters come from recognizable
    noreply@/ESP domains), so it leads the string."""
    sender = re.sub(r"\s+", " ", (from_address or "")).strip().lower()
    subj = re.sub(r"\s+", " ", (subject or "")).strip()
    bod = re.sub(r"\s+", " ", (body or "")).strip()[:body_chars]
    return f"From: {sender}\n{subj}\n{subj}\n{bod}".strip()
