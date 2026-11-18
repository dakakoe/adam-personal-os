"""No function may acquire a second pool connection while holding one —
with max_size=4 that deadlocked the listener on 2026-10-23."""
from __future__ import annotations

import inspect
import re

from fetcher import raw


def test_allowlist_upsert_reads_setting_before_acquiring() -> None:
    src = inspect.getsource(raw.upsert_group_allowlist_row)
    before, _, inside = src.partition("async with pool.acquire()")
    assert "auto_follow_max_members(pool)" in before
    assert "auto_follow_max_members(pool)" not in inside


def test_no_pool_call_inside_an_acquire_block_anywhere_in_raw() -> None:
    src = inspect.getsource(raw)
    for block in re.finditer(r"async with pool\.acquire\(\) as conn:\n((?:        .*\n|\n)+)", src):
        body = block.group(1)
        assert not re.search(r"await \w+\(pool[,)]", body), body[:200]
