"""Media for notes: images pasted into a page, and link previews.

Images: the bytes are sniffed (never trusted by extension or declared type),
capped, hashed for dedupe and written under the notes media dir; the DB row
is the index. Dimensions come from the file headers — no imaging library.

Link previews: one server-side fetch of the page's OpenGraph tags, cached
per URL. The fetch is guarded against SSRF: http(s) only, every hop's host
must resolve to a public address, bounded size and time, no credentials.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
import struct
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PREVIEW_BYTES = 1024 * 1024
PREVIEW_TIMEOUT_S = 6.0
PREVIEW_MAX_REDIRECTS = 3
PREVIEW_USER_AGENT = "Mozilla/5.0 (compatible; ADAM-notes/1.0; +https://github.com/dakakoe/adam-personal-os)"


class MediaError(ValueError):
    """A request that can be explained: wrong type, too big, unsafe URL."""


# --- images -----------------------------------------------------------------

@dataclass(frozen=True)
class ImageInfo:
    mime: str
    ext: str
    width: int | None
    height: int | None


def sniff_image(data: bytes) -> ImageInfo:
    """Type and size from the file's own bytes. PNG, JPEG, GIF, WebP."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return ImageInfo("image/png", "png", w, h)
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return ImageInfo("image/gif", "gif", w, h)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ImageInfo("image/webp", "webp", *_webp_size(data))
    if data[:3] == b"\xff\xd8\xff":
        return ImageInfo("image/jpeg", "jpg", *_jpeg_size(data))
    raise MediaError("not an image we can store (PNG, JPEG, GIF or WebP)")


def _jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seg_len
    return None, None


def _webp_size(data: bytes) -> tuple[int | None, int | None]:
    chunk = data[12:16]
    try:
        if chunk == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            b = data[21:25]
            w = 1 + (((b[1] & 0x3F) << 8) | b[0])
            h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
            return w, h
        if chunk == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return w, h
    except (struct.error, IndexError):
        pass
    return None, None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str | None, limit: int = 120) -> str | None:
    """The original filename, kept for display only: no paths, bounded."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return base[:limit] or None


# --- link previews -------------------------------------------------------------

def normalize_url(url: str) -> str:
    """The cache key: scheme+host lower-cased, fragment dropped, no credentials."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise MediaError("only http(s) links can be previewed")
    if parts.username or parts.password:
        raise MediaError("links with credentials are not previewed")
    netloc = parts.hostname.lower()
    if parts.port and parts.port not in (80, 443):
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def host_is_public(host: str) -> bool:
    """Every address the host resolves to must be routable on the internet —
    no loopback, private, link-local, multicast or reserved ranges. Fails
    closed on resolution errors."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


class _OGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            key = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "body":
            self._done = True          # everything we want is in <head>

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def parse_preview(html_text: str, base_url: str) -> dict[str, Any]:
    """OpenGraph first, then Twitter cards, then plain <title>/description."""
    p = _OGParser()
    try:
        p.feed(html_text)
    except Exception:  # noqa: BLE001 — a broken page still yields what was parsed
        pass
    m = p.meta
    title = m.get("og:title") or m.get("twitter:title") or " ".join(p.title_parts).strip()
    desc = m.get("og:description") or m.get("twitter:description") or m.get("description")
    image = m.get("og:image") or m.get("og:image:url") or m.get("twitter:image")
    site = m.get("og:site_name")
    if image:
        image = urljoin(base_url, image)
        if urlsplit(image).scheme not in ("http", "https"):
            image = None
    clean = lambda s: html.unescape(re.sub(r"\s+", " ", s)).strip() if s else None  # noqa: E731
    return {
        "title": (clean(title) or None) and clean(title)[:300],
        "description": (clean(desc) or None) and clean(desc)[:500],
        "image_url": image,
        "site_name": clean(site)[:100] if site else None,
    }


async def fetch_preview(url: str) -> dict[str, Any]:
    """GET the page with the guards above and parse its head. Returns the
    preview fields plus `status`; raises MediaError for unsafe URLs."""
    url = normalize_url(url)
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=PREVIEW_TIMEOUT_S,
                                 headers={"User-Agent": PREVIEW_USER_AGENT,
                                          "Accept": "text/html,application/xhtml+xml"}) as hc:
        for _ in range(PREVIEW_MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname or ""
            if not host_is_public(host):
                raise MediaError("that address is not reachable from here")
            async with hc.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = normalize_url(urljoin(current, r.headers["location"]))
                    continue
                ctype = r.headers.get("content-type", "")
                if r.status_code >= 400 or ("html" not in ctype and "xml" not in ctype):
                    return {"status": r.status_code, "title": None, "description": None,
                            "image_url": None, "site_name": None,
                            "error": None if r.status_code < 400 else f"http {r.status_code}"}
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= MAX_PREVIEW_BYTES:
                        break
                text = buf.decode(r.encoding or "utf-8", errors="replace")
                out = parse_preview(text, current)
                out.update({"status": r.status_code, "error": None})
                return out
    raise MediaError("too many redirects")


# --- video links ------------------------------------------------------------

_VIDEO_PATTERNS = (
    ("youtube", re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")),
    ("vimeo", re.compile(r"^https?://(?:www\.)?vimeo\.com/(?:video/)?(\d+)")),
    ("loom", re.compile(r"^https?://(?:www\.)?loom\.com/(?:share|embed)/([A-Za-z0-9]+)")),
)


def video_embed(url: str) -> dict[str, str] | None:
    """Provider + embed URL for a video link, or None. The editor does the
    same client-side; this is for the markdown→doc path and for tests."""
    for provider, rx in _VIDEO_PATTERNS:
        m = rx.match((url or "").strip())
        if m:
            vid = m.group(1)
            src = {
                "youtube": f"https://www.youtube-nocookie.com/embed/{vid}",
                "vimeo": f"https://player.vimeo.com/video/{vid}",
                "loom": f"https://www.loom.com/embed/{vid}",
            }[provider]
            return {"provider": provider, "id": vid, "src": src}
    return None
