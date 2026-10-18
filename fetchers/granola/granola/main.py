from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import httpx

from . import client as gclient
from .config import Config

log = logging.getLogger(__name__)


def _report_status(cfg: Config, status: str, reason: str | None = None) -> None:
    """Best-effort: report this source's health to merge_api (Sources page).
    Never fatal — a status-report failure must not affect ingest."""
    try:
        body = json.dumps({"status": status, "reason": reason}).encode("utf-8")
        req = urllib.request.Request(
            cfg.status_url, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {cfg.ingest_token}",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as e:
        log.warning("granola: status report (%s) failed: %s", status, e)


def _post_ingest(cfg: Config, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        cfg.ingest_url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {cfg.ingest_token}",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_api_key(cfg: Config) -> str | None:
    """The setup wizard's stored key (via merge_api's bearer-only internal
    endpoint) wins over the env var, so a key rotated in the UI takes effect
    on the next run. Env is the fallback / bootstrap path."""
    try:
        req = urllib.request.Request(
            cfg.key_url, headers={"Authorization": f"Bearer {cfg.ingest_token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            key = json.loads(resp.read().decode("utf-8")).get("secret")
        if key:
            log.info("granola: using key from merge_api (setup wizard)")
            return key
    except Exception as e:
        log.info("granola: no wizard key (%s) — falling back to env", e)
    return cfg.api_key


def run(cfg: Config) -> int:
    api_key = _resolve_api_key(cfg)
    if not api_key:
        _report_status(cfg, "needs_attention",
                       "no Granola API key — set it in the Setup wizard (/setup)")
        log.error("granola: no API key (wizard or GRANOLA_API_KEY) — exiting")
        return 1
    # Granola wants ISO without microseconds + a 'Z' suffix; a microsecond/
    # +00:00 form 400s.
    created_after = (datetime.now(timezone.utc) - timedelta(days=cfg.window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    client = gclient.GranolaClient(api_key, cfg.api_base, pause_s=cfg.request_pause_s)
    seen = ingested = skipped = no_summary = errored = 0
    try:
        try:
            summaries = list(client.iter_notes(created_after=created_after, page_size=cfg.page_size))
        except httpx.HTTPStatusError as e:
            # The list call goes through httpx (client.py), so a rejected API
            # key surfaces here as 401/403 — flag it for the Sources page.
            code = e.response.status_code
            if code in (401, 403):
                _report_status(cfg, "needs_attention",
                               f"Granola API key rejected ({code}) — rotate GRANOLA_API_KEY")
                log.error("granola list: %d (auth) — flagged needs_attention", code)
            else:
                log.error("granola list failed: %s", e)
            return 1
        except Exception:
            log.exception("granola list failed")
            return 1

        log.info("granola: %d notes since %s (window=%dd dry_run=%s)",
                 len(summaries), created_after, cfg.window_days, cfg.dry_run)

        for s in summaries:
            seen += 1
            note_id = s.get("id")
            if not note_id:
                continue
            try:
                note = client.get_note(note_id)
            except Exception:
                log.exception("granola get_note failed for %s", note_id)
                errored += 1
                continue

            summary = note.get("summary_markdown") or note.get("summary_text")
            if not summary or not summary.strip():
                no_summary += 1
                continue

            payload = {
                "source_id": note_id,
                "title": note.get("title"),
                "meeting_date": gclient.meeting_date(note),
                "attendees": gclient.attendees(note),
                "summary": summary,
                # No "force": the ingest endpoint is idempotent and skips
                # already-processed notes without an LLM call.
            }

            if cfg.dry_run:
                log.info("DRY would ingest: %s (%s)", (note.get("title") or "")[:50], note_id)
                continue

            try:
                out = _post_ingest(cfg, payload)
            except Exception as e:
                log.warning("ingest failed for %s (%s): %s", (note.get("title") or "")[:40], note_id, e)
                errored += 1
                continue

            if out.get("skipped") == "already_processed":
                skipped += 1
            else:
                ingested += 1
                verb = "re-ingested (edited)" if out.get("reingested") else "ingested"
                log.info("%s %s — %s", verb, (note.get("title") or "")[:50], out.get("suggested"))

        log.info(
            "granola done: seen=%d ingested=%d skipped=%d no_summary=%d errored=%d",
            seen, ingested, skipped, no_summary, errored,
        )
        # The list call succeeded, so the API key is valid → clear any stale
        # 'needs_attention'. (Per-note ingest errors aren't an auth problem.)
        _report_status(cfg, "ok", None)
        return 1 if errored else 0
    finally:
        client.close()
