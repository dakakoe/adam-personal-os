"""getUpdates offset persistence — the Telegram update_id cursor. Atomic file
write (adapted from alerter/state.py) so a crash mid-write can't corrupt it."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class State:
    offset: int | None = None   # next getUpdates offset (last update_id + 1)
    # Pending field-edit state, keyed by str(chat_id) so household users don't
    # collide. Each value: {"capture_id": str, "field": str, "message_id": int}.
    # When set for a chat, that chat's next *text* message is consumed as the edit.
    awaiting: dict | None = None

    def get_awaiting(self, chat_id) -> dict | None:
        return (self.awaiting or {}).get(str(chat_id))

    def set_awaiting(self, chat_id, value: dict | None) -> None:
        d = dict(self.awaiting or {})
        if value is None:
            d.pop(str(chat_id), None)
        else:
            d[str(chat_id)] = value
        self.awaiting = d or None

    @classmethod
    def load(cls, path: str) -> "State":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except Exception as e:  # noqa: BLE001
            log.warning("could not read state %s (%s); starting fresh", path, e)
            return cls()
        off = raw.get("offset")
        aw = raw.get("awaiting")
        return cls(offset=int(off) if off is not None else None,
                   awaiting=aw if isinstance(aw, dict) else None)

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "offset": self.offset,
            "awaiting": self.awaiting,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".telegram_bot.", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
