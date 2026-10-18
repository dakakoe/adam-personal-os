"""Etherscan V2 as a log source, for chains whose RPCs we cannot use.

Two problems force this. The droplet is a datacenter IP, so Cloudflare-fronted
public RPCs answer 403 (error 1010) there even though they work from a laptop.
And the Alchemy key on the box is Free tier, which caps eth_getLogs at a
10-block range — unusable for a backfill spanning millions of blocks.

Etherscan's V2 API covers Ethereum, Arbitrum and Polygon on the free plan with
one key (Base and BNB Chain need a paid plan). It also returns each log's
timestamp, which removes the separate block-time lookups the RPC path needs.

The class deliberately mirrors the Rpc interface (`url`, `block_number`,
`block_times`, `get_logs`) so the indexer does not care which source it holds.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("zerc20.etherscan")

from .indexer import _hexint   # noqa: E402 — shared tolerant hex parsing
from .rpc import redact       # noqa: E402 — shared URL/key redaction

BASE = "https://api.etherscan.io/v2/api"
PAGE = 1000          # max records per page on the free plan
MAX_PAGES = 10       # Etherscan stops paginating a window at 10,000 records
RATE_DELAY = 0.25    # free plan allows 5 requests/second


class EtherscanError(RuntimeError):
    pass


class WindowTooDense(EtherscanError):
    """The window holds more logs than Etherscan will paginate, so results would
    be silently truncated. The caller halves the range instead."""


class Etherscan:
    def __init__(self, client, api_key: str, chain_id: int, name: str):
        self._client = client
        self._key = api_key
        self._chain_id = chain_id
        self.name = name
        self._ts: dict[int, int] = {}      # block -> unix ts, harvested from logs

    @property
    def url(self) -> str:
        # No key in the value: this is only used to pick a chunk size and to log.
        return f"{BASE}?chainid={self._chain_id}"

    async def _get(self, params: dict, *, attempts: int = 4):
        params = {**params, "chainid": self._chain_id, "apikey": self._key}
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                r = await self._client.get(BASE, params=params)
                r.raise_for_status()
                body = r.json()
                result = body.get("result")
                # The proxy module speaks JSON-RPC and carries no "status" field;
                # only the data modules (logs, account, …) use status/message.
                if "status" not in body:
                    if body.get("error"):
                        raise EtherscanError(str(body["error"])[:120])
                    return result
                if body.get("status") == "1":
                    return result
                message = str(body.get("message") or "")
                # "No records found" is a legitimate empty window, not a failure.
                if "no records" in message.lower() or result == []:
                    return []
                if "rate limit" in str(result).lower() or "rate limit" in message.lower():
                    await asyncio.sleep(1.0 + attempt)
                    continue
                raise EtherscanError(f"{message}: {str(result)[:120]}")
            except EtherscanError:
                raise
            except Exception as e:  # noqa: BLE001
                last = redact(e)
                await asyncio.sleep(1.0 + attempt)
        raise EtherscanError(redact(f"{self.name} etherscan request failed: {last}"))

    async def block_number(self) -> int:
        result = await self._get({"module": "proxy", "action": "eth_blockNumber"})
        return _hexint(result)

    async def find_block_at(self, target_ts: int) -> int:
        """First block at or after `target_ts`.

        Etherscan answers this directly, so unlike the RPC path there is no
        30-call binary search — and without this the chains that have no
        hardcoded deployment block (Polygon, Kaia, BNB Chain) cannot start.
        """
        result = await self._get({"module": "block", "action": "getblocknobytime",
                                  "timestamp": int(target_ts), "closest": "after"})
        return int(str(result))

    async def block_times(self, numbers: list[int], *, batch_size: int = 100) -> dict[int, int]:
        """Served from timestamps already seen in logs; misses are looked up."""
        out = {n: self._ts[n] for n in numbers if n in self._ts}
        for n in [n for n in numbers if n not in out]:
            blk = await self._get({"module": "proxy", "action": "eth_getBlockByNumber",
                                   "tag": hex(n), "boolean": "false"})
            if blk and blk.get("timestamp"):
                out[n] = _hexint(blk.get("timestamp"))
            await asyncio.sleep(RATE_DELAY)
        return out

    async def _window(self, addresses: list[str], start: int, end: int) -> list[dict]:
        """Every log for these addresses in [start, end], following pagination."""
        window: list[dict] = []
        for address in addresses:
            page = 1
            while True:
                rows = await self._get({"module": "logs", "action": "getLogs",
                                        "address": address, "fromBlock": start,
                                        "toBlock": end, "page": page, "offset": PAGE})
                for row in rows or []:
                    block = _hexint(row.get("blockNumber"))
                    if row.get("timeStamp"):
                        self._ts[block] = _hexint(row.get("timeStamp"))
                    window.append({
                        "address": row["address"],
                        "topics": row["topics"],
                        "data": row.get("data") or "0x",
                        "blockNumber": row["blockNumber"],
                        "logIndex": row["logIndex"] or "0x0",
                        "transactionHash": row["transactionHash"],
                    })
                await asyncio.sleep(RATE_DELAY)
                if not rows or len(rows) < PAGE:
                    break
                if page >= MAX_PAGES:
                    # Past this Etherscan would just stop, and we would store a
                    # truncated window and advance the cursor over the gap.
                    raise WindowTooDense(f"{self.name}: >{MAX_PAGES * PAGE} logs in {start}-{end}")
                page += 1
        return window

    async def get_logs(self, addresses: list[str], from_block: int, to_block: int, chunk: int):
        """Yield (logs, window_end), shaped exactly like eth_getLogs results.

        Etherscan takes one address per call, so a chain with five watched
        contracts costs five requests per window — but it accepts very wide
        ranges (20M blocks answered in under 3s), so windows are large and the
        request count stays low. A window too dense to paginate is halved.
        """
        start = from_block
        while start <= to_block:
            end = min(start + chunk - 1, to_block)
            try:
                window = await self._window(addresses, start, end)
            except WindowTooDense as e:
                if chunk > 5_000:
                    chunk = max(5_000, chunk // 2)
                    log.info("%s: dense window, narrowing to %d blocks (%s)", self.name, chunk, e)
                    continue
                raise
            yield window, end
            start = end + 1
