from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import api, telegram, transcribe
from .config import Config
from .state import State

log = logging.getLogger(__name__)

HELP = (
    "🤖 Send me a note — text, a voice memo, or a 📷 photo of a payment slip — and "
    "I turn it into one structured item. I classify it automatically; you always "
    "Confirm (or Edit / Discard) before anything is created.\n"
    "\n"
    "I can capture:\n"
    "📋 Task — a to-do. \"call the bank Friday\", \"every weekday prep the report\" "
    "(recurring). Add a time, due date, project, contact, or sync it to a calendar.\n"
    "💼 Opportunity — a deal/partnership. \"maybe sponsor Token2049 with ADI\".\n"
    "📅 Event — something with a time, for your calendar. \"lunch with Alex Friday "
    "1pm\", \"Zoom with David at 3\" (makes a Google Meet). Can email an invite.\n"
    "💸 Expense — money out. \"spent 340 baht groceries at Tops\", \"50 usd taxi\".\n"
    "💰 Income — money in. \"got paid 5000 thb consulting\".\n"
    "🔀 Transfer — money between your OWN accounts. \"transfer from the user M. to "
    "SCB\", or a top-up slip — logged as a transfer, never as income.\n"
    "\n"
    "For expense/income I pull the amount, currency, category, payee and account "
    "automatically — usually just tap ✅ Confirm. Use ✏️ Edit to fix the amount, "
    "account, category, payee, date, or 🔀 switch the type.\n"
    "\n"
    "📷 Receipt photos: snap or forward a bank slip (SCB/KBank/PromptPay, Thai is "
    "fine) and I read the amount, payee and date off it. Send several at once — "
    "you get one card per slip.\n"
    "\n"
    "Tips: voice notes work everywhere (I transcribe them). While editing, your "
    "next text is the edit; a voice note or photo starts a fresh capture. /help shows this."
)


async def run(cfg: Config) -> None:
    model = transcribe.load_model(cfg)
    state = State.load(cfg.state_path)
    Path(cfg.voice_dir).mkdir(parents=True, exist_ok=True)
    log.info("ready: long-poll=%ss chat=%s api=%s", cfg.long_poll_timeout_s,
             cfg.allowed_chat_id, cfg.merge_api_base)

    async with httpx.AsyncClient(timeout=cfg.http_timeout_s) as client:
        while True:
            try:
                updates = await telegram.get_updates(
                    client, cfg.bot_token, state.offset, cfg.long_poll_timeout_s)
            except Exception as e:  # noqa: BLE001 — transient network/Telegram blips
                log.warning("getUpdates failed: %s; retrying", e)
                await asyncio.sleep(3)
                continue
            for u in updates:
                try:
                    await _handle(client, cfg, model, state, u)
                except Exception:
                    log.exception("update %s failed", u.get("update_id"))
                finally:
                    # advance the cursor even on failure so one bad update can't
                    # wedge the loop (it won't be re-delivered)
                    state.offset = u["update_id"] + 1
                    state.save(cfg.state_path)


async def _handle(client: httpx.AsyncClient, cfg: Config, model, state: State, u: dict) -> None:
    if "message" in u:
        await _handle_message(client, cfg, model, state, u["message"])
    elif "callback_query" in u:
        await _handle_callback(client, cfg, state, u["callback_query"])


async def _handle_message(client, cfg: Config, model, state: State, msg: dict) -> None:
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id not in cfg.allowed_chats:
        log.info("ignoring message from chat %s", chat_id)   # security gate — no reply
        return
    owner = cfg.allowed_chats[chat_id]   # 'me' | 'wife' | 'son' — who's capturing

    text: str | None = None
    is_voice = False
    if msg.get("voice"):
        text = await _transcribe_voice(client, cfg, model, msg["voice"], chat_id)
        if text is None:
            return
        is_voice = True
    elif msg.get("text"):
        t = msg["text"].strip()
        if t.startswith("/"):   # /start, /help, etc.
            state.set_awaiting(chat_id, None)   # a command cancels any pending edit
            await telegram.send_message(client, cfg.bot_token, chat_id, HELP)
            return
        text = t
    elif msg.get("photo"):
        # Payment-slip photo → vision capture. Telegram sends a media group as
        # separate photo messages (one card each); we handle each on its own.
        await _handle_photo(client, cfg, state, chat_id, owner, msg)
        return
    else:
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    "I handle text, voice notes, and receipt photos.")
        return

    # Edit-mode: a pending field-edit consumes a *text* reply (a voice note
    # falls through and starts a fresh capture instead).
    if state.get_awaiting(chat_id) and not is_voice:
        if await _apply_edit_text(client, cfg, state, chat_id, text):
            return

    # New capture — clears any stale edit-mode.
    state.set_awaiting(chat_id, None)
    try:
        result = await api.capture(client, cfg, text=text, owner=owner, source=(
            "telegram_voice" if msg.get("voice") else "telegram_text"))
    except Exception as e:  # noqa: BLE001
        log.exception("capture failed")
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    "⚠️ Couldn't process that — try again.")
        return

    if result.get("blocked"):   # finance-scoped member sent a non-finance note
        await telegram.send_message(client, cfg.bot_token, chat_id, result.get("summary") or "🚫 Not allowed.")
        return

    await telegram.send_message(
        client, cfg.bot_token, chat_id, result.get("summary") or "(no summary)",
        reply_markup=telegram.confirm_keyboard(result["id"]),
    )


async def _handle_photo(client, cfg: Config, state: State, chat_id: int, owner: str, msg: dict) -> None:
    """Download the largest photo size, send it to vision capture, and reply with
    the same confirm card used for text/voice. A photo starts a fresh capture."""
    state.set_awaiting(chat_id, None)
    sizes = msg.get("photo") or []
    if not sizes:
        return
    file_id = sizes[-1]["file_id"]            # last entry = largest resolution
    try:
        data = await telegram.download_file_bytes(client, cfg.bot_token, file_id)
    except Exception:  # noqa: BLE001
        log.exception("photo download failed")
        data = None
    if not data:
        await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Couldn't download that photo.")
        return
    img_b64 = base64.standard_b64encode(data).decode()
    caption = (msg.get("caption") or "").strip()
    try:
        result = await api.capture(client, cfg, text=caption, source="telegram_photo",
                                   owner=owner, image_b64=img_b64, image_media_type="image/jpeg")
    except Exception:  # noqa: BLE001
        log.exception("photo capture failed")
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    "⚠️ Couldn't read that receipt — try a clearer photo.")
        return
    if result.get("blocked") or not result.get("id"):
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    result.get("summary") or "🚫 Couldn't log that.")
        return
    await telegram.send_message(
        client, cfg.bot_token, chat_id, result.get("summary") or "(no summary)",
        reply_markup=telegram.confirm_keyboard(result["id"]),
    )


async def _transcribe_voice(client, cfg: Config, model, voice: dict, chat_id: int) -> str | None:
    dest = os.path.join(cfg.voice_dir, f"{voice['file_id'][:24]}.oga")
    try:
        fp = await telegram.get_file_path(client, cfg.bot_token, voice["file_id"])
        if not fp:
            return None
        await telegram.download_file(client, cfg.bot_token, fp, dest)
        text, lang = await transcribe.transcribe_file(model, dest, cfg.language)
        if not text:
            await telegram.send_message(client, cfg.bot_token, chat_id,
                                        "🎙️ Couldn't hear any speech — try again.")
            return None
        log.info("transcribed voice (lang=%s, %d chars)", lang, len(text))
        return text
    except Exception:
        log.exception("voice transcription failed")
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    "⚠️ Couldn't transcribe that voice note.")
        return None
    finally:
        try:
            os.unlink(dest)
        except OSError:
            pass


def _quick_date(which: str) -> str | None:
    """Resolve a quick-date button to a YYYY-MM-DD string in the user's tz.
    'clr' (and anything unknown) → None, meaning clear the date."""
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    if which == "today":
        return today.isoformat()
    if which == "tom":
        return (today + timedelta(days=1)).isoformat()
    if which == "wk":
        return (today + timedelta(days=7)).isoformat()
    return None


def _card_keyboard(capture_id: str, body: dict) -> dict:
    """Keyboard for a re-rendered card: pre-create → Confirm row; a created
    event (post-create edit mode) → the edit menu with Save."""
    if body.get("created"):
        return telegram.edit_menu_keyboard(
            capture_id, "event", created=True, all_day=body.get("all_day", False))
    return telegram.confirm_keyboard(capture_id)


async def _patch_and_refresh(client, cfg: Config, capture_id: str, chat_id, message_id, fields: dict) -> None:
    """Apply a field edit and re-render the card back to the Confirm view."""
    code, body = await api.patch_capture(client, cfg, capture_id, fields)
    if code == 200:
        await telegram.edit_message_text(
            client, cfg.bot_token, chat_id, message_id,
            body.get("summary") or "(updated)",
            reply_markup=_card_keyboard(capture_id, body))
    elif code == 409:
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id, "↩️ Already actioned.")
    elif code == 404:
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id, "⚠️ Capture not found.")
    else:
        await telegram.send_message(client, cfg.bot_token, chat_id,
                                    f"⚠️ {body.get('detail', 'Update failed')}")


async def _apply_edit_text(client, cfg: Config, state: State, chat_id, text: str) -> bool:
    """Consume a text reply as input for a pending field-edit (for THIS chat).
    Returns True if the text was handled (so it shouldn't start a new capture)."""
    aw = state.get_awaiting(chat_id) or {}
    cid = aw.get("capture_id")
    field = aw.get("field")
    mid = aw.get("message_id")
    if not cid or not field:
        state.set_awaiting(chat_id, None)
        return False

    # Amount accepts "340" or "340 usd" → split into amount + optional currency.
    if field == "amount":
        toks = text.replace(",", "").split()
        patch = {}
        try:
            patch["amount"] = float(toks[0])
        except (ValueError, IndexError):
            await telegram.send_message(client, cfg.bot_token, chat_id, "💵 Send a number, e.g. 340 (or 50 usd).")
            return True
        if len(toks) > 1 and toks[1].isalpha():
            patch["currency"] = toks[1].upper()
        code, body = await api.patch_capture(client, cfg, cid, patch)
        state.set_awaiting(chat_id, None)
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, mid,
            body.get("summary") or "(updated)", reply_markup=_card_keyboard(cid, body)) if code == 200 \
            else await telegram.send_message(client, cfg.bot_token, chat_id, f"⚠️ {body.get('detail','Update failed')}")
        return True

    if field == "payee":
        code, body = await api.patch_capture(client, cfg, cid, {"payee_text": text})
        state.set_awaiting(chat_id, None)
        if code == 200:
            await telegram.edit_message_text(client, cfg.bot_token, chat_id, mid,
                body.get("summary") or "(updated)", reply_markup=_card_keyboard(cid, body))
        return True

    if field == "category_search":
        state.set_awaiting(chat_id, None)
        try:
            res = await api.search_capture_categories(client, cfg, cid, text)
        except Exception:
            log.exception("category search failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Category search failed.")
            return True
        choices = res.get("choices") or []
        typed = res.get("typed") or text
        header = (f"Categories matching \"{typed}\":" if choices else f"No category matches \"{typed}\".")
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, mid, header,
            reply_markup=telegram.category_pick_keyboard(cid, choices, typed))
        return True

    # Simple single-field edits → PATCH {field: text}. A 400 (bad date/time)
    # keeps edit-mode so the user can retry the value without re-tapping.
    if field in ("title", "due_date", "event_date", "event_time", "location", "task_time", "txn_date"):
        code, body = await api.patch_capture(client, cfg, cid, {field: text})
        if code == 400:
            hint = {
                "due_date": "📅 Couldn't read that as a date — try e.g. 2026-06-10.",
                "event_date": "📅 Couldn't read that as a date — try e.g. 2026-06-10.",
                "txn_date": "📅 Couldn't read that as a date — try e.g. 2026-06-10.",
                "event_time": "🕐 Use HH:MM or HH:MM-HH:MM (e.g. 14:00-15:00).",
                "task_time": "🕐 Use HH:MM (e.g. 14:00).",
            }.get(field, f"⚠️ {body.get('detail', 'Invalid value')}")
            await telegram.send_message(client, cfg.bot_token, chat_id, hint)
            return True
        state.set_awaiting(chat_id, None)
        if code == 200:
            await telegram.edit_message_text(
                client, cfg.bot_token, chat_id, mid, body.get("summary") or "(updated)",
                reply_markup=_card_keyboard(cid, body))
        else:
            await telegram.send_message(client, cfg.bot_token, chat_id,
                                        f"⚠️ {body.get('detail', 'Update failed')}")
        return True

    if field == "person_search":
        state.set_awaiting(chat_id, None)   # results are picked via buttons now
        try:
            res = await api.search_capture_people(client, cfg, cid, text)
        except Exception:
            log.exception("person search failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Contact search failed.")
            return True
        choices = res.get("choices") or []
        typed = res.get("typed") or text
        header = (f"Contacts matching \"{typed}\":" if choices else f"No matches for \"{typed}\".")
        await telegram.edit_message_text(
            client, cfg.bot_token, chat_id, mid, header,
            reply_markup=telegram.person_pick_keyboard(cid, choices, typed))
        return True

    state.set_awaiting(chat_id, None)
    return False


async def _handle_callback(client, cfg: Config, state: State, cq: dict) -> None:
    cqid = cq["id"]
    if (cq.get("from") or {}).get("id") not in cfg.allowed_chats:
        await telegram.answer_callback_query(client, cfg.bot_token, cqid, "Not authorized")
        return
    await telegram.answer_callback_query(client, cfg.bot_token, cqid)

    actor_owner = cfg.allowed_chats.get((cq.get("from") or {}).get("id"), "me")
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    parts = data.split(":")
    action, capture_id = parts[0], (parts[1] if len(parts) > 1 else "")
    if not capture_id:
        return

    if action == "dis":
        code, _ = await api.discard(client, cfg, capture_id)
        state.set_awaiting(chat_id, None)
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id, "❌ Discarded")
    elif action == "edit":
        # open the Edit sub-menu (need the type for the person label + Date
        # gating; a created event gets the post-create menu with Save)
        code, body = await api.get_capture(client, cfg, capture_id)
        if code != 200:
            await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                             "↩️ Already actioned." if code == 409 else "⚠️ Not found.")
            return
        await telegram.edit_message_reply_markup(
            client, cfg.bot_token, chat_id, message_id,
            telegram.edit_menu_keyboard(capture_id, body.get("type"), body.get("conference", False),
                                        all_day=body.get("all_day", False),
                                        created=body.get("created", False)))
    elif action == "eback":
        # restore the full card (+ Confirm row, or ✏️ Edit-event when created)
        state.set_awaiting(chat_id, None)
        code, body = await api.get_capture(client, cfg, capture_id)
        if code == 200:
            rm = (telegram.created_event_keyboard(capture_id) if body.get("created")
                  else telegram.confirm_keyboard(capture_id))
            await telegram.edit_message_text(
                client, cfg.bot_token, chat_id, message_id, body.get("summary") or "(no summary)",
                reply_markup=rm)
        else:
            await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                             "↩️ Already actioned." if code == 409 else "⚠️ Not found.")
    elif action == "ename":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "title", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "✏️ Send the new name.",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "edate":
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.date_quick_keyboard(capture_id))
    elif action == "edateinput":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "due_date", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "📅 Send a date (e.g. 2026-06-10).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "dq":
        which = parts[2] if len(parts) > 2 else "clr"
        d = _quick_date(which)
        fields = {"clear_due_date": True} if d is None else {"due_date": d}
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, fields)
    elif action == "eedate":   # event date sub-menu
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.event_date_quick_keyboard(capture_id))
    elif action == "edq":      # event date quick-pick (always a concrete date)
        which = parts[2] if len(parts) > 2 else "today"
        d = _quick_date(which) or _quick_date("today")
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"event_date": d})
    elif action == "eedateinput":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "event_date", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "📅 Send a date (e.g. 2026-06-10).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "etime":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "event_time", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "🕐 Send a time (e.g. 14:00 or 14:00-15:00).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "eloc":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "location", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "📍 Send a location.",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "emeet":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"toggle_conference": True})
    elif action == "ead":      # event all-day toggle
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"toggle_all_day": True})
    elif action == "saveev":   # push post-create edits to the real Google event
        code, body = await api.update_event(client, cfg, capture_id)
        if code == 200:
            await telegram.edit_message_text(
                client, cfg.bot_token, chat_id, message_id,
                body.get("summary") or "✅ Updated",
                reply_markup=telegram.created_event_keyboard(capture_id))
        else:
            await telegram.send_message(client, cfg.bot_token, chat_id,
                                        f"⚠️ {body.get('detail', 'Calendar update failed')}")
    elif action == "ettime":   # task time (typed)
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "task_time", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "🕐 Send a time (e.g. 14:00).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "erepeat":
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.repeat_menu_keyboard(capture_id))
    elif action == "rep":      # set a non-weekly repeat preset → back to card
        val = parts[2] if len(parts) > 2 else "none"
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"repeat": val})
    elif action == "repweek":  # open the weekly day-picker
        code, body = await api.get_capture(client, cfg, capture_id)
        sel = body.get("repeat_weekdays", []) if code == 200 else []
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.weekly_days_keyboard(capture_id, sel))
    elif action == "rwd":      # toggle a weekday → re-render the picker
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        code, body = await api.patch_capture(client, cfg, capture_id, {"toggle_weekday": idx})
        sel = body.get("repeat_weekdays", []) if code == 200 else []
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.weekly_days_keyboard(capture_id, sel))
    elif action == "edur":
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.duration_keyboard(capture_id))
    elif action == "dur":
        m = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        fields = {"clear_duration": True} if m == 0 else {"duration_min": m}
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, fields)
    elif action == "ecal":     # choose a calendar to sync to on confirm
        try:
            accounts = await api.list_calendars(client, cfg)
        except Exception:
            log.exception("list calendars failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Couldn't load calendars.")
            return
        if not accounts:
            await telegram.send_message(client, cfg.bot_token, chat_id, "No connected calendars.")
            return
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.calendar_picker_keyboard(capture_id, accounts))
    elif action == "setcal":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        try:
            accounts = await api.list_calendars(client, cfg)
        except Exception:
            accounts = []
        if not (0 <= idx < len(accounts)):
            return
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"calendar_account": accounts[idx]})
    elif action == "caloff":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"clear_calendar": True})
    elif action == "eperson":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "person_search", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "👤 Type a name to search your contacts.",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "eproj":
        try:
            projects = await api.list_projects(client, cfg)
        except Exception:
            log.exception("list projects failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Couldn't load projects.")
            return
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.project_keyboard(capture_id, projects))
    elif action == "setp":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"person_choice_idx": idx})
    elif action == "setptyped":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"use_typed_person": True})
    elif action == "setpclr":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"clear_person": True})
    elif action == "setpr":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"project_idx": idx})
    elif action == "setprclr":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"clear_project": True})
    elif action == "chg":
        # show the type-choice keyboard (reclassify on pick). Finance-scoped
        # members only ever see expense↔income — no CRM types.
        kb = (telegram.type_choice_keyboard_finance(capture_id) if actor_owner in ("wife", "son")
              else telegram.type_choice_keyboard_full(capture_id))
        await telegram.edit_message_text(
            client, cfg.bot_token, chat_id, message_id,
            (msg.get("text") or "") + "\n\nChange type to:", reply_markup=kb)
    # --- expense / income edits ---
    elif action == "eamt":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "amount", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "💵 Send the amount (e.g. 340, or 50 usd).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "epay":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "payee", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "👤 Send the payee / who was paid.",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "eacct":
        try:
            accounts = await api.capture_accounts(client, cfg, capture_id)
        except Exception:
            log.exception("load accounts failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Couldn't load accounts.")
            return
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.account_pick_keyboard(capture_id, accounts))
    elif action == "setacc":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"account_choice_idx": idx})
    elif action in ("esrc", "edst"):   # transfer: pick source / destination account
        try:
            accounts = await api.capture_accounts(client, cfg, capture_id)
        except Exception:
            log.exception("load accounts failed")
            await telegram.send_message(client, cfg.bot_token, chat_id, "⚠️ Couldn't load accounts.")
            return
        cb = "setsrc" if action == "esrc" else "setdst"
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.account_pick_keyboard(capture_id, accounts, cb=cb))
    elif action in ("setsrc", "setdst"):
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        field = "from_account_choice_idx" if action == "setsrc" else "to_account_choice_idx"
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {field: idx})
    elif action == "ecat":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "category_search", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "🏷 Type to search categories.",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "setcat":
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
        if idx is None:
            return
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"category_choice_idx": idx})
    elif action == "setcatx":
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"clear_category": True})
    elif action == "etdate":
        await telegram.edit_message_reply_markup(client, cfg.bot_token, chat_id, message_id,
                                                 telegram.txn_date_keyboard(capture_id))
    elif action == "tdq":
        which = parts[2] if len(parts) > 2 else "today"
        today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
        d = today if which == "today" else (today - timedelta(days=1))
        await _patch_and_refresh(client, cfg, capture_id, chat_id, message_id, {"txn_date": d.isoformat()})
    elif action == "etdinput":
        state.set_awaiting(chat_id, {"capture_id": capture_id, "field": "txn_date", "message_id": message_id})
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                         "📅 Send a date (e.g. 2026-06-14).",
                                         reply_markup=telegram.back_keyboard(capture_id))
    elif action == "set":
        target = {"t": "task", "o": "opportunity", "e": "event",
                  "x": "expense", "i": "income", "r": "transfer"}.get(parts[2] if len(parts) > 2 else "")
        if not target:
            return
        try:
            res = await api.reclassify(client, cfg, capture_id, target)
            await telegram.edit_message_text(
                client, cfg.bot_token, chat_id, message_id, res.get("summary") or "(no summary)",
                reply_markup=telegram.confirm_keyboard(capture_id))
        except Exception:
            log.exception("reclassify failed")
            await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id,
                                             "⚠️ Couldn't change type.")
    elif action == "cfm":
        state.set_awaiting(chat_id, None)
        code, body = await api.confirm(client, cfg, capture_id)
        # Event whose contact has emails → ask who to invite before creating.
        if code == 200 and body.get("needs") == "invite_choice":
            await telegram.edit_message_text(
                client, cfg.bot_token, chat_id, message_id,
                body.get("summary") or "Send an invite?",
                reply_markup=telegram.invite_keyboard(capture_id, body.get("emails") or []))
            return
        if code == 200:
            out = body.get("summary") or "✅ Created"
        elif code == 409:
            out = "↩️ Already actioned."
        else:
            out = f"⚠️ {body.get('detail', 'Could not create')}"
        rm = telegram.created_event_keyboard(capture_id) if (code == 200 and body.get("editable")) else None
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id, out, reply_markup=rm)
    elif action == "inv":
        sub = parts[2] if len(parts) > 2 else "x"
        idx = int(sub) if sub.isdigit() else None   # 'x' (skip) → None
        code, body = await api.invite(client, cfg, capture_id, idx)
        if code == 200:
            out = body.get("summary") or "✅ Done"
        elif code == 409:
            out = "↩️ Already actioned."
        else:
            out = f"⚠️ {body.get('detail', 'Could not create')}"
        rm = telegram.created_event_keyboard(capture_id) if (code == 200 and body.get("editable")) else None
        await telegram.edit_message_text(client, cfg.bot_token, chat_id, message_id, out, reply_markup=rm)
