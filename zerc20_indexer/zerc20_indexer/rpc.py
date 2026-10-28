"""A small JSON-RPC client that survives public endpoints.

Every provider disagrees about how wide an eth_getLogs range may be, and none of
them say so up front: Base's public node caps at 2,000 blocks, BSC's answers
"limit exceeded", Alchemy takes 10,000. Rather than hard-code a table that rots,
`get_logs` starts from the chain's configured chunk and halves it whenever a
range complaint comes back, so a slow endpoint degrades instead of failing.
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger("zerc20.rpc")

# Provider URLs carry the API key in the path (/v2/<key>) or a query parameter.
# httpx puts the full URL in its exception text, so an unredacted error message
# writes the key into the service log. Everything user-visible goes through this.
_SECRET_IN_URL = re.compile(r"(/v[12]/)[^/?\s]+|([?&](?:apikey|api_key)=)[^&\s]+", re.I)


def redact(text: str) -> str:
    return _SECRET_IN_URL.sub(lambda m: (m.group(1) or m.group(2)) + "<redacted>", str(text))

# Substrings providers use when a log range is too wide or returns too much.
_RANGE_HINTS = ("range", "limit exceeded", "too many", "exceed", "more than", "query returned")
_RETRY_HINTS = ("timeout", "rate limit", "429", "too many requests", "502", "503",
                "capacity", "403", "forbidden")   # public nodes answer 403 when throttling


class RpcError(RuntimeError):
    """`range_error` means "ask for fewer blocks", anything else means "this
    endpoint is unhappy" — the two need opposite responses, and a 403 answered
    by halving the range just fails five times more slowly."""

    def __init__(self, message: str, *, range_error: bool = False):
        super().__init__(message)
        self.range_error = range_error


class Rpc:
    def __init__(self, client, urls: list[str], name: str, *, pace: float = 0.12):
        # Several endpoints per chain: public nodes block, rate-limit and 403
        # by region, so a dead one is rotated away from rather than fatal.
        self._client = client
        self._urls = list(urls)
        self._i = 0
        self.name = name
        self._id = 0
        self._pace = pace          # gap between log windows; hammering earns a 403

    @property
    def url(self) -> str:
        return self._urls[self._i]

    def _next_endpoint(self) -> bool:
        if self._i + 1 >= len(self._urls):
            return False
        self._i += 1
        log.info("%s: switching endpoint to %s", self.name, redact(self.url))
        return True

    async def call(self, method: str, params: list, *, attempts: int = 4):
        last: Exception | None = None
        while True:
            for attempt in range(attempts):
                self._id += 1
                payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
                try:
                    r = await self._client.post(self.url, json=payload)
                    if r.status_code == 429 or r.status_code >= 500:
                        raise RpcError(f"http {r.status_code}")
                    r.raise_for_status()
                    body = r.json()
                    if "error" in body:
                        raise RpcError(str(body["error"].get("message", body["error"]))[:200])
                    return body.get("result")
                except Exception as e:  # noqa: BLE001 — one blip shouldn't end a backfill
                    last = redact(e)
                    msg = str(e).lower()
                    if any(h in msg for h in _RANGE_HINTS):
                        raise RpcError(redact(e), range_error=True)   # caller narrows instead
                    if not any(h in msg for h in _RETRY_HINTS) and attempt >= 1:
                        break
                    # A 429 means "you are going too fast", so wait much longer
                    # than for a transient blip; Kaia's public node hands these
                    # out steadily during a backfill.
                    throttled = "429" in msg or "rate limit" in msg or "too many" in msg
                    await asyncio.sleep((4.0 if throttled else 1.5) * (attempt + 1))
            if not self._next_endpoint():
                raise RpcError(f"{self.name} {method} failed on every endpoint: {redact(last)}")

    async def batch(self, calls: list[tuple[str, list]]) -> list:
        """One HTTP round trip for many calls; falls back to serial on refusal."""
        if not calls:
            return []
        payload = []
        for method, params in calls:
            self._id += 1
            payload.append({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        try:
            r = await self._client.post(self.url, json=payload)
            r.raise_for_status()
            body = r.json()
            if isinstance(body, dict):
                raise RpcError("batch rejected")
            by_id = {row["id"]: row for row in body}
            return [by_id.get(p["id"], {}).get("result") for p in payload]
        except Exception as e:  # noqa: BLE001
            log.debug("%s batch failed (%s), falling back to serial", self.name, redact(e))
            return [await self.call(m, p) for m, p in calls]

    async def block_number(self) -> int:
        return int(await self.call("eth_blockNumber", []), 16)

    async def block_timestamp(self, number: int) -> int:
        blk = await self.call("eth_getBlockByNumber", [hex(number), False])
        if not blk:
            raise RpcError(f"{self.name}: block {number} not found")
        return int(blk["timestamp"], 16)

    async def block_times(self, numbers: list[int], *, batch_size: int = 100) -> dict[int, int]:
        """Timestamps for many blocks. eth_getLogs omits them on most providers."""
        out: dict[int, int] = {}
        for i in range(0, len(numbers), batch_size):
            window = numbers[i:i + batch_size]
            results = await self.batch([("eth_getBlockByNumber", [hex(n), False]) for n in window])
            for n, blk in zip(window, results):
                if blk and blk.get("timestamp"):
                    out[n] = int(blk["timestamp"], 16)
        return out

    async def find_block_at(self, target_ts: int) -> int:
        """First block at or after `target_ts`, by binary search (~30 calls)."""
        lo, hi = 1, await self.block_number()
        while lo < hi:
            mid = (lo + hi) // 2
            try:
                ts = await self.block_timestamp(mid)
            except RpcError:
                lo = mid + 1                 # pruned/missing block: search above it
                continue
            if ts < target_ts:
                lo = mid + 1
            else:
                hi = mid
        return lo

    async def get_logs(self, addresses: list[str], from_block: int, to_block: int, chunk: int):
        """Yield (logs, window_end) chunk by chunk, narrowing on range errors."""
        start = from_block
        while start <= to_block:
            end = min(start + chunk - 1, to_block)
            try:
                logs = await self.call("eth_getLogs", [{
                    "address": addresses,
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                }])
            except RpcError as e:
                if not e.range_error:
                    raise
                if chunk > 200:
                    chunk = max(200, chunk // 2)
                    log.info("%s: narrowing range to %d blocks (%s)", self.name, chunk, redact(e)[:80])
                    continue
                raise
            yield logs or [], end
            start = end + 1
            if self._pace:
                await asyncio.sleep(self._pace)
