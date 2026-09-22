"""Telegram Bot API helpers (async httpx). Long-poll getUpdates + the
send/edit/answer methods needed for the inline-confirm flow."""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


async def get_updates(client: httpx.AsyncClient, token: str, offset: int | None, timeout_s: int) -> list[dict]:
    params: dict = {
        "timeout": timeout_s,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    # read timeout must exceed the long-poll window (set on the client)
    resp = await client.get(_api(token, "getUpdates"), params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("result") or []


async def get_file_path(client: httpx.AsyncClient, token: str, file_id: str) -> str | None:
    resp = await client.get(_api(token, "getFile"), params={"file_id": file_id})
    resp.raise_for_status()
    return (resp.json().get("result") or {}).get("file_path")


async def download_file(client: httpx.AsyncClient, token: str, file_path: str, dest: str) -> bool:
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = await client.get(url)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(resp.content)
    return True


async def download_file_bytes(client: httpx.AsyncClient, token: str, file_id: str) -> bytes | None:
    """Resolve a Telegram file_id → its bytes (used for photo-receipt capture)."""
    path = await get_file_path(client, token, file_id)
    if not path:
        return None
    resp = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
    resp.raise_for_status()
    return resp.content


async def send_message(
    client: httpx.AsyncClient, token: str, chat_id: int, text: str, reply_markup: dict | None = None,
) -> dict | None:
    body: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        resp = await client.post(_api(token, "sendMessage"), json=body)
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as e:  # noqa: BLE001
        log.error("sendMessage failed: %s", _err(e))
        return None


async def edit_message_text(
    client: httpx.AsyncClient, token: str, chat_id: int, message_id: int,
    text: str, reply_markup: dict | None = None,
) -> bool:
    body: dict = {"chat_id": chat_id, "message_id": message_id, "text": text,
                  "disable_web_page_preview": True}
    body["reply_markup"] = reply_markup or {"inline_keyboard": []}
    try:
        resp = await client.post(_api(token, "editMessageText"), json=body)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.error("editMessageText failed: %s", _err(e))
        return False


async def edit_message_reply_markup(
    client: httpx.AsyncClient, token: str, chat_id: int, message_id: int, reply_markup: dict | None,
) -> bool:
    """Swap only the inline keyboard, leaving the card text intact (used to open
    the Edit sub-menus without disturbing the summary)."""
    body: dict = {"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": reply_markup or {"inline_keyboard": []}}
    try:
        resp = await client.post(_api(token, "editMessageReplyMarkup"), json=body)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.error("editMessageReplyMarkup failed: %s", _err(e))
        return False


async def answer_callback_query(
    client: httpx.AsyncClient, token: str, callback_query_id: str, text: str | None = None,
) -> None:
    body: dict = {"callback_query_id": callback_query_id}
    if text:
        body["text"] = text
    try:
        resp = await client.post(_api(token, "answerCallbackQuery"), json=body)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("answerCallbackQuery failed: %s", _err(e))


def _err(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"{e} — {e.response.text[:200]}"
    return str(e)


def confirm_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Confirm", "callback_data": f"cfm:{capture_id}"},
        {"text": "✏️ Edit", "callback_data": f"edit:{capture_id}"},
        {"text": "❌ Discard", "callback_data": f"dis:{capture_id}"},
    ]]}


def created_event_keyboard(capture_id: str) -> dict:
    """On the '✅ Added to calendar' message — reopen editing for the real event."""
    return {"inline_keyboard": [[
        {"text": "✏️ Edit event", "callback_data": f"edit:{capture_id}"},
    ]]}


def edit_menu_keyboard(capture_id: str, ctype: str | None, conference: bool = False,
                       all_day: bool = False, created: bool = False) -> dict:
    """The Edit sub-menu. Events get the full meeting controls (date, time,
    all-day, location, Google-Meet toggle); tasks get a due Date; opportunities
    neither. Person label is context-aware (Invitee for a meeting, Contact
    otherwise). `created=True` is the post-create variant for an event that's
    already on Google Calendar: only patchable fields + a Save button (the
    invitee/Meet/type are fixed at creation)."""
    if ctype == "event":
        ad = "📆 All-day: on" if all_day else "📆 All-day: off"
        if created:
            return {"inline_keyboard": [
                [{"text": "✏️ Name", "callback_data": f"ename:{capture_id}"},
                 {"text": "📅 Date", "callback_data": f"eedate:{capture_id}"}],
                [{"text": "🕐 Time", "callback_data": f"etime:{capture_id}"},
                 {"text": ad, "callback_data": f"ead:{capture_id}"}],
                [{"text": "📍 Location", "callback_data": f"eloc:{capture_id}"}],
                [{"text": "💾 Save to calendar", "callback_data": f"saveev:{capture_id}"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
            ]}
        meet = "🎥 Meet: on" if conference else "🎥 Meet: off"
        return {"inline_keyboard": [
            [{"text": "✏️ Name", "callback_data": f"ename:{capture_id}"},
             {"text": "📅 Date", "callback_data": f"eedate:{capture_id}"}],
            [{"text": "🕐 Time", "callback_data": f"etime:{capture_id}"},
             {"text": ad, "callback_data": f"ead:{capture_id}"}],
            [{"text": "📍 Location", "callback_data": f"eloc:{capture_id}"},
             {"text": "👤 Invitee", "callback_data": f"eperson:{capture_id}"}],
            [{"text": meet, "callback_data": f"emeet:{capture_id}"},
             {"text": "📁 Project", "callback_data": f"eproj:{capture_id}"}],
            [{"text": "🔀 Type", "callback_data": f"chg:{capture_id}"},
             {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
        ]}
    if ctype == "task":
        return {"inline_keyboard": [
            [{"text": "✏️ Name", "callback_data": f"ename:{capture_id}"},
             {"text": "📅 Date", "callback_data": f"edate:{capture_id}"}],
            [{"text": "🕐 Time", "callback_data": f"ettime:{capture_id}"},
             {"text": "🔁 Repeat", "callback_data": f"erepeat:{capture_id}"}],
            [{"text": "⏱ Duration", "callback_data": f"edur:{capture_id}"},
             {"text": "👤 Contact", "callback_data": f"eperson:{capture_id}"}],
            [{"text": "📁 Project", "callback_data": f"eproj:{capture_id}"},
             {"text": "📅 Calendar", "callback_data": f"ecal:{capture_id}"}],
            [{"text": "🔀 Type", "callback_data": f"chg:{capture_id}"},
             {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
        ]}
    if ctype in ("expense", "income"):
        return {"inline_keyboard": [
            [{"text": "💵 Amount", "callback_data": f"eamt:{capture_id}"},
             {"text": "🏦 Account", "callback_data": f"eacct:{capture_id}"}],
            [{"text": "🏷 Category", "callback_data": f"ecat:{capture_id}"},
             {"text": "👤 Payee", "callback_data": f"epay:{capture_id}"}],
            [{"text": "📅 Date", "callback_data": f"etdate:{capture_id}"},
             {"text": "🔀 Type", "callback_data": f"chg:{capture_id}"}],
            [{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
        ]}
    if ctype == "transfer":
        return {"inline_keyboard": [
            [{"text": "💵 Amount", "callback_data": f"eamt:{capture_id}"},
             {"text": "📅 Date", "callback_data": f"etdate:{capture_id}"}],
            [{"text": "🏦 From (source)", "callback_data": f"esrc:{capture_id}"},
             {"text": "🏦 To (dest)", "callback_data": f"edst:{capture_id}"}],
            [{"text": "🔀 Type", "callback_data": f"chg:{capture_id}"},
             {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
        ]}
    # opportunity (no date/time/repeat)
    return {"inline_keyboard": [
        [{"text": "✏️ Name", "callback_data": f"ename:{capture_id}"}],
        [{"text": "👤 Contact", "callback_data": f"eperson:{capture_id}"},
         {"text": "📁 Project", "callback_data": f"eproj:{capture_id}"}],
        [{"text": "🔀 Type", "callback_data": f"chg:{capture_id}"},
         {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


def account_pick_keyboard(capture_id: str, accounts: list[dict], cb: str = "setacc") -> dict:
    """Account picker. `cb` is the callback prefix — 'setacc' for expense/income,
    'setsrc'/'setdst' to fill a transfer's source / destination leg."""
    rows = [[{"text": f"🏦 {a['name']}", "callback_data": f"{cb}:{capture_id}:{i}"}]
            for i, a in enumerate(accounts)]
    rows.append([{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def category_pick_keyboard(capture_id: str, cats: list[dict], typed: str | None) -> dict:
    rows = [[{"text": f"🏷 {c['label']}", "callback_data": f"setcat:{capture_id}:{i}"}]
            for i, c in enumerate(cats)]
    rows.append([{"text": "✖ Clear", "callback_data": f"setcatx:{capture_id}"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def txn_date_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Today", "callback_data": f"tdq:{capture_id}:today"},
         {"text": "Yesterday", "callback_data": f"tdq:{capture_id}:yest"}],
        [{"text": "✏️ Type a date", "callback_data": f"etdinput:{capture_id}"},
         {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


def type_choice_keyboard_full(capture_id: str) -> dict:
    """Type switcher incl. the budget transaction types."""
    return {"inline_keyboard": [
        [{"text": "📋 Task", "callback_data": f"set:{capture_id}:t"},
         {"text": "💼 Opp", "callback_data": f"set:{capture_id}:o"},
         {"text": "📅 Event", "callback_data": f"set:{capture_id}:e"}],
        [{"text": "💸 Expense", "callback_data": f"set:{capture_id}:x"},
         {"text": "💰 Income", "callback_data": f"set:{capture_id}:i"},
         {"text": "🔀 Transfer", "callback_data": f"set:{capture_id}:r"}],
    ]}


def type_choice_keyboard_finance(capture_id: str) -> dict:
    """Expense / income / transfer — for finance-scoped members (no CRM types)."""
    return {"inline_keyboard": [
        [{"text": "💸 Expense", "callback_data": f"set:{capture_id}:x"},
         {"text": "💰 Income", "callback_data": f"set:{capture_id}:i"},
         {"text": "🔀 Transfer", "callback_data": f"set:{capture_id}:r"}],
        [{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


def repeat_menu_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Doesn't repeat", "callback_data": f"rep:{capture_id}:none"},
         {"text": "Every day", "callback_data": f"rep:{capture_id}:daily"}],
        [{"text": "Weekdays", "callback_data": f"rep:{capture_id}:weekdays"},
         {"text": "Weekly…", "callback_data": f"repweek:{capture_id}"}],
        [{"text": "Monthly", "callback_data": f"rep:{capture_id}:monthly"},
         {"text": "Yearly", "callback_data": f"rep:{capture_id}:yearly"}],
        [{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


_DOW_LABELS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def weekly_days_keyboard(capture_id: str, selected: list[int]) -> dict:
    sel = set(selected or [])
    row = [{"text": ("✓" + _DOW_LABELS[d]) if d in sel else _DOW_LABELS[d],
            "callback_data": f"rwd:{capture_id}:{d}"} for d in range(7)]
    return {"inline_keyboard": [row, [{"text": "✅ Done", "callback_data": f"eback:{capture_id}"}]]}


def calendar_picker_keyboard(capture_id: str, accounts: list[str]) -> dict:
    # One button per connected account (callback carries the index — 64-byte cap),
    # plus a way to turn calendar sync off.
    rows = [[{"text": f"📅 {a}", "callback_data": f"setcal:{capture_id}:{i}"}]
            for i, a in enumerate(accounts)]
    rows.append([{"text": "✖ Off", "callback_data": f"caloff:{capture_id}"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def duration_keyboard(capture_id: str) -> dict:
    opts = [(15, "15m"), (30, "30m"), (45, "45m"), (60, "1h"), (90, "1.5h"), (120, "2h")]
    rows, cur = [], []
    for m, lbl in opts:
        cur.append({"text": lbl, "callback_data": f"dur:{capture_id}:{m}"})
        if len(cur) == 3:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    rows.append([{"text": "✖ None", "callback_data": f"dur:{capture_id}:0"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def event_date_quick_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Today", "callback_data": f"edq:{capture_id}:today"},
         {"text": "Tomorrow", "callback_data": f"edq:{capture_id}:tom"},
         {"text": "+1 week", "callback_data": f"edq:{capture_id}:wk"}],
        [{"text": "✏️ Type a date", "callback_data": f"eedateinput:{capture_id}"}],
        [{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


def back_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [[{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}]]}


def date_quick_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Today", "callback_data": f"dq:{capture_id}:today"},
         {"text": "Tomorrow", "callback_data": f"dq:{capture_id}:tom"},
         {"text": "+1 week", "callback_data": f"dq:{capture_id}:wk"}],
        [{"text": "✏️ Type a date", "callback_data": f"edateinput:{capture_id}"},
         {"text": "Clear", "callback_data": f"dq:{capture_id}:clr"}],
        [{"text": "◀ Back", "callback_data": f"eback:{capture_id}"}],
    ]}


def person_pick_keyboard(capture_id: str, choices: list[dict], typed: str | None) -> dict:
    # One button per match (callback carries only the index — see the API note
    # on the 64-byte callback_data cap), plus keep-as-typed / clear / back.
    rows = [[{"text": f"👤 {c['name']}", "callback_data": f"setp:{capture_id}:{i}"}]
            for i, c in enumerate(choices)]
    if typed:
        rows.append([{"text": f"➕ Keep \"{typed[:28]}\" (new contact)",
                      "callback_data": f"setptyped:{capture_id}"}])
    rows.append([{"text": "✖ Clear", "callback_data": f"setpclr:{capture_id}"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def project_keyboard(capture_id: str, projects: list[dict]) -> dict:
    rows = [[{"text": f"📁 {p['name']}", "callback_data": f"setpr:{capture_id}:{i}"}]
            for i, p in enumerate(projects)]
    rows.append([{"text": "✖ None", "callback_data": f"setprclr:{capture_id}"},
                 {"text": "◀ Back", "callback_data": f"eback:{capture_id}"}])
    return {"inline_keyboard": rows}


def type_choice_keyboard(capture_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "📋 Task", "callback_data": f"set:{capture_id}:t"},
        {"text": "💼 Opp", "callback_data": f"set:{capture_id}:o"},
        {"text": "📅 Event", "callback_data": f"set:{capture_id}:e"},
    ]]}


def invite_keyboard(capture_id: str, emails: list[str]) -> dict:
    # One button per email (label = the address; callback carries only the
    # index, since callback_data is capped at 64 bytes), plus a skip.
    rows = [[{"text": f"📧 {e}", "callback_data": f"inv:{capture_id}:{i}"}]
            for i, e in enumerate(emails)]
    rows.append([{"text": "Don't invite", "callback_data": f"inv:{capture_id}:x"}])
    return {"inline_keyboard": rows}
