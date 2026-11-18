"""Image sniffing from bytes, preview parsing, and the SSRF guard."""
from __future__ import annotations

import struct

import pytest

from merge_api import notes_media as nm


def _png(w: int, h: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x06" + b"\x00" * 20


def test_sniff_png_gif_jpeg_webp() -> None:
    assert nm.sniff_image(_png(640, 480)) == nm.ImageInfo("image/png", "png", 640, 480)
    gif = b"GIF89a" + struct.pack("<HH", 12, 34) + b"\x00" * 10
    assert nm.sniff_image(gif) == nm.ImageInfo("image/gif", "gif", 12, 34)
    # JPEG: SOI, an APP0 segment, then SOF0 with height=100 width=200
    jpeg = (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
            + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 100, 200) + b"\x03" + b"\x00" * 9)
    assert nm.sniff_image(jpeg) == nm.ImageInfo("image/jpeg", "jpg", 200, 100)
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 10 + struct.pack("<HH", 300, 150) + b"\x00" * 4
    assert nm.sniff_image(webp) == nm.ImageInfo("image/webp", "webp", 300, 150)


def test_sniff_rejects_non_images() -> None:
    with pytest.raises(nm.MediaError):
        nm.sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(nm.MediaError):
        nm.sniff_image(b"%PDF-1.4")


def test_safe_name_strips_paths() -> None:
    assert nm.safe_name("../../etc/passwd") == "passwd"
    assert nm.safe_name("C:\\Users\\me\\shot.png") == "shot.png"
    assert nm.safe_name("") is None


def test_normalize_url_rules() -> None:
    assert nm.normalize_url("HTTPS://Example.com/A?b=1#frag") == "https://example.com/A?b=1"
    assert nm.normalize_url("https://example.com") == "https://example.com/"
    for bad in ("ftp://x.org/f", "file:///etc/passwd", "https://user:pw@example.com/", "javascript:alert(1)"):
        with pytest.raises(nm.MediaError):
            nm.normalize_url(bad)


def test_private_hosts_are_not_public(monkeypatch) -> None:
    def fake(host, *a, **k):
        table = {"evil.test": "10.0.0.5", "lo.test": "127.0.0.1", "link.test": "169.254.169.254",
                 "v6.test": "fd00::1", "ok.test": "93.184.216.34"}
        return [(None, None, None, None, (table[host], 0))]
    monkeypatch.setattr(nm.socket, "getaddrinfo", fake)
    for h in ("evil.test", "lo.test", "link.test", "v6.test"):
        assert nm.host_is_public(h) is False, h
    assert nm.host_is_public("ok.test") is True


def test_unresolvable_host_fails_closed(monkeypatch) -> None:
    def boom(*a, **k):
        raise nm.socket.gaierror
    monkeypatch.setattr(nm.socket, "getaddrinfo", boom)
    assert nm.host_is_public("nope.invalid") is False


def test_parse_preview_prefers_opengraph_and_resolves_images() -> None:
    page = """<html><head><title>Fallback &amp; title</title>
      <meta property="og:title" content="OG  Title">
      <meta name="description" content="plain desc">
      <meta property="og:image" content="/img/cover.png">
      <meta property="og:site_name" content="Site"></head><body><p>x</p></body></html>"""
    out = nm.parse_preview(page, "https://example.com/post/1")
    assert out == {"title": "OG Title", "description": "plain desc",
                   "image_url": "https://example.com/img/cover.png", "site_name": "Site"}


def test_parse_preview_falls_back_to_title_and_drops_bad_image_schemes() -> None:
    page = '<head><title>Just a title</title><meta property="og:image" content="javascript:x"></head>'
    out = nm.parse_preview(page, "https://example.com/")
    assert out["title"] == "Just a title" and out["image_url"] is None


def test_video_embed_recognises_providers() -> None:
    assert nm.video_embed("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1")["src"].endswith("/embed/dQw4w9WgXcQ")
    assert nm.video_embed("https://youtu.be/dQw4w9WgXcQ")["provider"] == "youtube"
    assert nm.video_embed("https://vimeo.com/123456")["src"] == "https://player.vimeo.com/video/123456"
    assert nm.video_embed("https://www.loom.com/share/abc123DEF")["provider"] == "loom"
    assert nm.video_embed("https://example.com/watch?v=x") is None
