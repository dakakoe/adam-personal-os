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
    degraded: bool = False
    # Signature of the last alert we actually SENT (not just observed), so a
    # changed problem set re-alerts but a steady one only re-pings on cadence.
    last_alert_signature: str = ""
    last_alert_at: datetime | None = None

    @classmethod
    def load(cls, path: str) -> "State":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except Exception as e:  # noqa: BLE001 — corrupt state shouldn't wedge alerts
            log.warning("could not read state %s (%s); starting fresh", path, e)
            return cls()
        ts = raw.get("last_alert_at")
        return cls(
            degraded=bool(raw.get("degraded", False)),
            last_alert_signature=str(raw.get("last_alert_signature", "")),
            last_alert_at=datetime.fromisoformat(ts) if ts else None,
        )

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "degraded": self.degraded,
            "last_alert_signature": self.last_alert_signature,
            "last_alert_at": self.last_alert_at.isoformat() if self.last_alert_at else None,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        # Atomic write so a crash mid-write can't corrupt the dedup state.
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".alerter.", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
