"""The sender resolves a recipient by id, then username, then a dialog warm-up."""
from __future__ import annotations

import asyncio

import pytest

from fetcher import outbox


class _Pool:
    def __init__(self, username): self.username = username
    async def fetchval(self, sql, *a): return self.username


class _Client:
    def __init__(self, known_after_dialogs=False, by_username=False):
        self.calls = []; self.dialogs_loaded = False
        self.known_after_dialogs, self.by_username = known_after_dialogs, by_username

    async def get_input_entity(self, peer):
        self.calls.append(peer)
        if isinstance(peer, str):
            if self.by_username: return ("by-username", peer)
            raise ValueError("no such username")
        if self.dialogs_loaded and self.known_after_dialogs: return ("by-id", peer)
        raise ValueError("Could not find the input entity")

    async def get_dialogs(self, limit=None): self.dialogs_loaded = True


def test_username_fallback() -> None:
    c = _Client(by_username=True)
    out = asyncio.run(outbox.resolve_recipient(c, _Pool("someone"), 42))
    assert out == ("by-username", "someone") and not c.dialogs_loaded


def test_dialog_warmup_fallback() -> None:
    c = _Client(known_after_dialogs=True)
    out = asyncio.run(outbox.resolve_recipient(c, _Pool(None), 42))
    assert out[0] == "by-id" and c.dialogs_loaded


def test_unresolvable_is_explained() -> None:
    with pytest.raises(ValueError) as e:
        asyncio.run(outbox.resolve_recipient(_Client(), _Pool(None), 42))
    assert "Open the chat once" in str(e.value)



def test_sent_message_is_recorded(monkeypatch) -> None:
    calls = []

    async def fake_record(pool, client, msg, *, voice_dir, download_voice):
        calls.append((msg, voice_dir, download_voice)); return True

    monkeypatch.setattr(outbox.raw, "record_message", fake_record)

    class Cfg:
        voice_dir = "/tmp/v"

    class Msg:
        id, chat_id, sender_id = 77, 555, None

    class Client:
        async def get_me(self):
            class Me: id = 42
            return Me()

    class Pool:
        def __init__(self): self.exec = []
        async def execute(self, sql, *args): self.exec.append(args)

    pool = Pool(); msg = Msg()
    assert asyncio.run(outbox.record_sent(pool, Client(), msg, Cfg())) is True
    assert calls == [(msg, "/tmp/v", False)]
    assert pool.exec == [(555, 77, 42)]                       # our own id stamped on the row
    assert asyncio.run(outbox.record_sent(pool, Client(), msg, None)) is False
