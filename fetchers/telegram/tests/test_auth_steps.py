"""A session Telegram revoked is retired, not reused."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fetcher import auth_steps


def test_session_file_matches_telethon_naming(tmp_path: Path) -> None:
    assert auth_steps.session_file("/x/memory") == Path("/x/memory.session")
    assert auth_steps.session_file("/x/memory.session") == Path("/x/memory.session")


def test_retire_session_keeps_the_file_and_side_files(tmp_path: Path) -> None:
    base = tmp_path / "memory"
    (tmp_path / "memory.session").write_bytes(b"key")
    (tmp_path / "memory.session-journal").write_bytes(b"j")
    dest = auth_steps.retire_session(str(base), now=datetime(2026, 10, 18, 3, 15, tzinfo=timezone.utc))
    assert dest == tmp_path / "memory.session.revoked-20261018-031500"
    assert dest.read_bytes() == b"key"
    assert not (tmp_path / "memory.session").exists()
    assert (tmp_path / "memory.session.revoked-20261018-031500-journal").exists()


def test_retire_session_without_a_file_is_a_no_op(tmp_path: Path) -> None:
    assert auth_steps.retire_session(str(tmp_path / "memory")) is None
