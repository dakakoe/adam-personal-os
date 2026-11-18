"""Thin client for the Granola official API (https://public-api.granola.ai/v1).

Endpoints used:
  GET /notes?created_after=&cursor=&page_size=   → list note summaries
  GET /notes/{id}                                → full note incl. summary_markdown

Auth: Authorization: Bearer grn_…  (Settings → Connectors → API keys).
Only notes with a generated AI summary are returned by the API.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)


class GranolaClient:
    def __init__(self, api_key: str, api_base: str, *, pause_s: float = 0.25):
        self._http = httpx.Client(
            base_url=api_base,
            headers={"Authorization": f"Bearer {api_key}", "accept": "application/json"},
            timeout=30.0,
        )
        self._pause_s = pause_s

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        # One retry on 429 with a short backoff (burst limit is 25/5s).
        for attempt in range(2):
            resp = self._http.get(path, params=params)
            if resp.status_code == 429 and attempt == 0:
                time.sleep(2.0)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def iter_notes(self, *, created_after: str, page_size: int) -> Iterator[dict]:
        """Yield note summaries created after the given ISO timestamp,
        following pagination cursors."""
        cursor = None
        while True:
            params: dict[str, Any] = {"created_after": created_after, "page_size": page_size}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/notes", params)
            for n in data.get("notes") or []:
                yield n
            if not data.get("hasMore"):
                return
            cursor = data.get("cursor")
            if not cursor:
                return
            time.sleep(self._pause_s)

    def get_note(self, note_id: str) -> dict:
        time.sleep(self._pause_s)
        return self._get(f"/notes/{note_id}")


def meeting_date(note: dict) -> str | None:
    """Best meeting timestamp: the calendar event's start if present, else the
    note's created_at. Defensive about the calendar_event shape."""
    ce = note.get("calendar_event") or {}
    for key in ("start", "start_time", "start_at", "starts_at"):
        v = ce.get(key)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict):
            inner = v.get("dateTime") or v.get("date_time") or v.get("date")
            if inner:
                return inner
    return note.get("created_at")


def attendees(note: dict) -> list[dict]:
    """[{name, email}] from the note's attendees (best effort)."""
    out = []
    for a in note.get("attendees") or []:
        if not isinstance(a, dict):
            continue
        out.append({"name": a.get("name"), "email": a.get("email")})
    return out
