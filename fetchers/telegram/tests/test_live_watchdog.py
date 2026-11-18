"""A dead connection must raise, not wait forever."""
from __future__ import annotations

import asyncio

import pytest

from fetcher import live


class _Client:
    def __init__(self, connected=True, hang=False, fail=False):
        self._c, self._hang, self._fail = connected, hang, fail

    def is_connected(self):
        return self._c

    async def __call__(self, request):
        if self._hang:
            await asyncio.sleep(10)
        if self._fail:
            raise ConnectionError("gone")
        return {"ok": True}


def test_probe_passes_on_a_live_client() -> None:
    asyncio.run(live.heartbeat_once(_Client(), timeout_s=1))


def test_probe_raises_when_disconnected() -> None:
    with pytest.raises(live.LiveStalled):
        asyncio.run(live.heartbeat_once(_Client(connected=False), timeout_s=1))


def test_probe_raises_on_timeout_and_on_error() -> None:
    with pytest.raises(live.LiveStalled):
        asyncio.run(live.heartbeat_once(_Client(hang=True), timeout_s=0.1))
    with pytest.raises(live.LiveStalled):
        asyncio.run(live.heartbeat_once(_Client(fail=True), timeout_s=1))
