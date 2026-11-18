"""Thin merge_api client (localhost, bearer). All DB/LLM/business logic lives
in merge_api; the worker just shuttles text in and summaries out."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def _headers(cfg) -> dict:
    return {"Authorization": f"Bearer {cfg.bearer_token}"}


async def capture(client: httpx.AsyncClient, cfg, *, text: str, source: str, owner: str = "me",
                  image_b64: str | None = None, image_media_type: str = "image/jpeg") -> dict:
    body: dict = {"text": text, "source": source, "owner": owner}
    if image_b64:
        body["image_b64"] = image_b64
        body["image_media_type"] = image_media_type
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture",
        json=body, headers=_headers(cfg), timeout=90,   # vision adds a little latency
    )
    r.raise_for_status()
    return r.json()


async def reclassify(client: httpx.AsyncClient, cfg, capture_id: str, target_type: str) -> dict:
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/reclassify",
        json={"target_type": target_type}, headers=_headers(cfg), timeout=60,
    )
    r.raise_for_status()
    return r.json()


async def patch_capture(client: httpx.AsyncClient, cfg, capture_id: str, fields: dict) -> tuple[int, dict]:
    r = await client.patch(
        f"{cfg.merge_api_base}/api/capture/{capture_id}",
        json=fields, headers=_headers(cfg), timeout=60,
    )
    return r.status_code, _safe_json(r)


async def get_capture(client: httpx.AsyncClient, cfg, capture_id: str) -> tuple[int, dict]:
    r = await client.get(
        f"{cfg.merge_api_base}/api/capture/{capture_id}",
        headers=_headers(cfg), timeout=30,
    )
    return r.status_code, _safe_json(r)


async def search_capture_people(client: httpx.AsyncClient, cfg, capture_id: str, q: str) -> dict:
    r = await client.get(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/people",
        params={"q": q}, headers=_headers(cfg), timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def list_projects(client: httpx.AsyncClient, cfg) -> list[dict]:
    r = await client.get(
        f"{cfg.merge_api_base}/api/projects",
        headers=_headers(cfg), timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def capture_accounts(client: httpx.AsyncClient, cfg, capture_id: str) -> list[dict]:
    r = await client.get(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/accounts",
        headers=_headers(cfg), timeout=30,
    )
    r.raise_for_status()
    return r.json().get("choices") or []


async def search_capture_categories(client: httpx.AsyncClient, cfg, capture_id: str, q: str) -> dict:
    r = await client.get(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/categories",
        params={"q": q}, headers=_headers(cfg), timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def list_calendars(client: httpx.AsyncClient, cfg) -> list[str]:
    r = await client.get(
        f"{cfg.merge_api_base}/api/calendars",
        headers=_headers(cfg), timeout=30,
    )
    r.raise_for_status()
    return [a["account"] for a in (r.json().get("accounts") or [])]


async def confirm(client: httpx.AsyncClient, cfg, capture_id: str) -> tuple[int, dict]:
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/confirm",
        json={}, headers=_headers(cfg), timeout=60,
    )
    return r.status_code, (_safe_json(r))


async def invite(client: httpx.AsyncClient, cfg, capture_id: str, index: int | None) -> tuple[int, dict]:
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/invite",
        json=({"index": index} if index is not None else {}),
        headers=_headers(cfg), timeout=60,
    )
    return r.status_code, _safe_json(r)


async def update_event(client: httpx.AsyncClient, cfg, capture_id: str) -> tuple[int, dict]:
    """Push post-create edits of a confirmed event capture to Google Calendar."""
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/update-event",
        json={}, headers=_headers(cfg), timeout=60,
    )
    return r.status_code, _safe_json(r)


async def discard(client: httpx.AsyncClient, cfg, capture_id: str) -> tuple[int, dict]:
    r = await client.post(
        f"{cfg.merge_api_base}/api/capture/{capture_id}/discard",
        json={}, headers=_headers(cfg), timeout=30,
    )
    return r.status_code, (_safe_json(r))


def _safe_json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {"detail": r.text[:200]}
