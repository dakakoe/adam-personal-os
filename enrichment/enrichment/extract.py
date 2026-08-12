"""Regex extractors for identity/affiliation signals.

Each `extract_*` returns a list of (signal_type, normalized_value, raw_match)
tuples. Callers de-duplicate by (signal_type, value); raw_match is kept for
evidence/debugging.

Precision is preferred over recall. We reject obvious false positives at the
normalizer (e.g. linkedin.com/feed — that's a route, not a profile) and
require non-empty handle slugs.
"""

from __future__ import annotations

import re

# Email: RFC-lite. Doesn't try to be perfect — won't match weird quoted-locals,
# but those don't appear in chat text in practice.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# LinkedIn profile: /in/<slug> or /pub/<slug>/<more>. We capture only /in/ for
# now — /pub/ has different shape and is mostly deprecated.
_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:[a-z]+\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{2,80})/?",
    re.IGNORECASE,
)

# Twitter / X. Handles are 1-15 chars [A-Za-z0-9_]. Lots of false positives at
# this URL shape — twitter.com/share, /intent/tweet, /search — handled by the
# X_RESERVED denylist.
_X_RE = re.compile(
    # \b anchor prevents matches inside other hosts ("dropbox.com/s/..." was
    # capturing "s" as an X handle because the substring "x.com/s/" matched).
    r"(?:https?://)?(?:www\.)?\b(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})\b",
    re.IGNORECASE,
)
_X_RESERVED = {
    "share", "intent", "home", "search", "settings", "explore", "i", "compose",
    "notifications", "messages", "tos", "privacy", "help", "about", "login",
    "signup", "logout", "hashtag", "lists", "topics", "moments", "bookmarks",
}

# Instagram handles are 1-30 chars [A-Za-z0-9._].
_INSTAGRAM_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?",
    re.IGNORECASE,
)
_INSTAGRAM_RESERVED = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "share", "about", "developer", "legal", "help", "press",
}

# GitHub usernames are 1-39 chars, [A-Za-z0-9-], cannot start/end with hyphen.
_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9\-]{0,37}[A-Za-z0-9])?)\b",
    re.IGNORECASE,
)
_GITHUB_RESERVED = {
    "marketplace", "trending", "topics", "collections", "events", "sponsors",
    "settings", "notifications", "about", "pricing", "enterprise", "features",
    "team", "security", "login", "signup", "explore", "search", "issues",
    "pulls", "codespaces", "new", "join",
}

# A "personal website" — strict: must have http(s) scheme, must not be one of
# the platforms we already extract above. We keep only the host.
_WEBSITE_RE = re.compile(
    r"https?://(?:www\.)?([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)\b",
    re.IGNORECASE,
)
_WEBSITE_BLOCKLIST_HOSTS = {
    # Platforms we extract specifically
    "linkedin.com", "linkedin.cn",
    "twitter.com", "x.com", "t.co",
    "instagram.com",
    "github.com", "gist.github.com",
    "t.me", "telegram.me", "telegram.org",
    # Hosts that almost always appear as references, not the person's own site
    "youtube.com", "youtu.be", "tiktok.com",
    "facebook.com", "fb.com",
    "wikipedia.org", "wikimedia.org",
    "google.com", "goo.gl", "docs.google.com", "drive.google.com",
    "medium.com",
    "notion.so", "notion.site",
    "spotify.com", "open.spotify.com",
    "apple.com", "apps.apple.com",
    "amazon.com", "amzn.to",
    # Ephemeral / hosting
    "imgur.com", "i.imgur.com",
    "dropbox.com",
    "bit.ly", "tinyurl.com",
}


SignalHit = tuple[str, str, str]  # (signal_type, normalized_value, raw_match)

# Telegram channel/handle pattern. Only used in bio mode — in conversation
# bodies, t.me links almost always reference other channels, not the writer.
_TELEGRAM_RE = re.compile(
    r"(?:https?://)?\bt\.me/([A-Za-z0-9_]{4,32})\b",
    re.IGNORECASE,
)
_TELEGRAM_RESERVED = {"share", "joinchat", "addstickers", "iv", "proxy"}

# Bare @handle: bio convention is "ping me @username" referring to a
# Telegram handle. Requires non-word char (or start) before @ so we don't
# capture the local-part of an email as a handle.
_AT_HANDLE_RE = re.compile(
    r"(?:^|[^\w@])@([A-Za-z0-9_]{4,32})\b",
)
_AT_HANDLE_RESERVED = {"username", "channel", "admin", "bot"}

# Bare host (no scheme) — bio-only. Requires a dot, 2-6 char TLD, host length
# >= 6 chars, and rejects common file extensions that look like TLDs.
_BARE_HOST_RE = re.compile(
    r"\b([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.[a-z]{2,6}(?:\.[a-z]{2,6})?)\b",
    re.IGNORECASE,
)
# TLDs that are almost always file extensions in chat, not domains.
_TLD_FILE_EXT_DENY = {
    "py", "go", "js", "ts", "jsx", "tsx", "md", "txt", "log", "csv", "json",
    "xml", "yml", "yaml", "html", "htm", "css", "sql", "sh", "bash", "rb",
    "rs", "java", "c", "cpp", "h", "hpp", "lua", "php", "swift", "kt", "kts",
    "ipynb", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "tar",
    "gz", "bz2", "rar", "7z", "exe", "dmg", "iso", "img", "apk", "ipa", "png",
    "jpg", "jpeg", "gif", "svg", "webp", "mp3", "mp4", "wav", "flac", "mov",
    "avi", "mkv", "ogg",
}


def extract_all(text: str, *, mode: str = "strict") -> list[SignalHit]:
    """Run extractors on `text` and return a deduplicated list of hits.

    mode='strict' (default, for conversation bodies): email, linkedin, x,
    instagram, github. Skips websites and t.me because in chat those are
    overwhelmingly third-party references.

    mode='liberal' (for bios): everything in strict + websites + telegram
    handles. Bios are short and self-asserted, so the URL→identity claim
    is reliable.
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    hits: list[SignalHit] = []
    iterator = _iter_liberal if mode == "liberal" else _iter_strict
    for hit in iterator(text):
        key = (hit[0], hit[1])
        if key in seen:
            continue
        seen.add(key)
        hits.append(hit)

    # Drop website hits whose value is just the domain part of an email
    # we already extracted (e.g. "jane@acme.com" should not also produce
    # "website:acme.com" as a separate signal).
    email_domains = {h[1].split("@", 1)[1] for h in hits if h[0] == "email" and "@" in h[1]}
    if email_domains:
        hits = [h for h in hits if not (h[0] == "website" and h[1] in email_domains)]
    return hits


def _iter_strict(text: str):
    for m in _EMAIL_RE.findall(text):
        v = m.lower().strip()
        if v:
            yield ("email", v, m)

    for slug in _LINKEDIN_RE.findall(text):
        v = slug.lower().rstrip("/")
        if v and len(v) >= 2:
            yield ("linkedin", v, slug)

    for handle in _X_RE.findall(text):
        v = handle.lower().lstrip("@")
        if v and v not in _X_RESERVED:
            yield ("x", v, handle)

    for handle in _INSTAGRAM_RE.findall(text):
        v = handle.lower().rstrip("/").lstrip("@")
        # Reject pure-dots/underscores
        if v and v not in _INSTAGRAM_RESERVED and any(c.isalnum() for c in v):
            yield ("instagram", v, handle)

    for handle in _GITHUB_RE.findall(text):
        v = handle.lower()
        if v and v not in _GITHUB_RESERVED:
            yield ("github", v, handle)

    # Strict mode intentionally skips website + telegram — see _iter_liberal
    # for the bio-mode versions that include them.


def _iter_liberal(text: str):
    """Strict extractors plus website, t.me handles, bare hosts, and bare
    @handles. Use this on bios, where URL-shaped things are self-asserted
    identity (vs. conversation, where they're third-party references)."""
    yield from _iter_strict(text)

    # http(s)://... hosts
    for host in _WEBSITE_RE.findall(text):
        v = host.lower()
        root = ".".join(v.split(".")[-2:])
        if root in _WEBSITE_BLOCKLIST_HOSTS or v in _WEBSITE_BLOCKLIST_HOSTS:
            continue
        yield ("website", v, host)

    # t.me/handle
    for handle in _TELEGRAM_RE.findall(text):
        v = handle.lower()
        if v and v not in _TELEGRAM_RESERVED:
            yield ("telegram_handle", v, handle)

    # Bare @handle (Telegram bio convention)
    for handle in _AT_HANDLE_RE.findall(text):
        v = handle.lower()
        if v and v not in _AT_HANDLE_RESERVED:
            yield ("telegram_handle", v, "@" + handle)

    # Bare hosts (no scheme), e.g. "olgavox.com", "021.wtf"
    for host in _BARE_HOST_RE.findall(text):
        v = host.lower()
        if len(v) < 6:
            continue
        tld = v.rsplit(".", 1)[-1]
        if tld in _TLD_FILE_EXT_DENY:
            continue
        root = ".".join(v.split(".")[-2:])
        if root in _WEBSITE_BLOCKLIST_HOSTS or v in _WEBSITE_BLOCKLIST_HOSTS:
            continue
        yield ("website", v, host)


def _iter_all(text: str):
    """Back-compat alias used internally before mode-switching; kept so
    older callers (and tests) that imported _iter_all still work."""
    yield from _iter_strict(text)


def sample_context(text: str, match: str, around: int = 120) -> str:
    """Return a window of `text` around the first occurrence of `match`,
    clipped to `around` chars on each side. Used to store evidence so the
    operator can sanity-check why a signal was flagged."""
    if not text or not match:
        return ""
    idx = text.lower().find(match.lower())
    if idx < 0:
        return text[: around * 2]
    start = max(0, idx - around)
    end = min(len(text), idx + len(match) + around)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet
