from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import Config


def make_client(cfg: Config) -> TelegramClient:
    Path(cfg.session_path).parent.mkdir(parents=True, exist_ok=True)
    # Telethon appends .session to whatever name you pass.
    return TelegramClient(cfg.session_path, cfg.api_id, cfg.api_hash)


async def ensure_authorized(client: TelegramClient, phone: str) -> None:
    """Interactive first-time login. After the first successful login the
    session file persists and this just returns immediately."""
    await client.connect()
    if await client.is_user_authorized():
        return

    await client.send_code_request(phone)
    code = input(f"Enter the Telegram login code sent to {phone}: ").strip()
    try:
        await client.sign_in(phone=phone, code=code)
    except SessionPasswordNeededError:
        import getpass

        pw = getpass.getpass("Two-factor cloud password: ")
        await client.sign_in(password=pw)
