"""Haiku extraction over meeting recaps (Phase 2).

Given a Granola AI summary + title + attendee list + the user's project
list, returns structured action items, opportunities, and people
mentioned. The merge_api ingest endpoint then resolves the people names
against canonical.person and writes memory.suggestion rows.

We feed the *summary* (Granola's already-structured AI notes), not the
raw verbatim transcript — it's cleaner, cheaper, and already has a
"Next Steps" section the model can lean on.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You extract actionable structure from a meeting recap for a \
busy founder/BD operator (the user). He runs several crypto/Web3 projects and \
wants his meetings turned into: a one-line recap, concrete action items, \
business opportunities (deals/partnerships), and a list of named people who \
came up (so he can follow up with contacts in his CRM).

Rules:
- Be precise and terse. Don't invent items that aren't supported by the recap.
- action_items: concrete next steps. Capture the owner if stated (the user, \
Alex, Jordan, etc.) and any person the task is *with*. Only include real \
to-dos, not vague aspirations.
- opportunities: deals, partnerships, sponsorships, revenue paths, or \
"things we could do with person/company X." Pick the closest stage:
    intro          = first contact / exploring
    conversations  = actively discussing terms
    mou            = agreed in principle / MoU stage
    contract       = contract/signature in progress
    active         = live / ongoing / closed-won
- project_slug: choose the ONE project this meeting most relates to from the \
provided list, or null if none fit.
- people_mentioned: specific humans discussed (NOT the user himself, NOT generic \
roles) who the user might want to follow up with. For each, give a `reason` — \
the concrete thing to follow up ON (what was said, asked, promised, or left \
open with them). Only include a person if there is a real reason to reach \
out; if someone was named only in passing with nothing to act on, OMIT them \
entirely. A bare name with no reason is noise — leave it out.
"""


def _tool_schema(project_slugs: list[str]) -> dict[str, Any]:
    slug_enum = project_slugs + ["__none__"]
    return {
        "name": "record_meeting_extraction",
        "description": "Record the structured extraction from a meeting recap.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recap": {
                    "type": "string",
                    "description": "A 1-3 sentence plain-English recap of what happened + why it matters.",
                },
                "project_slug": {
                    "type": "string",
                    "enum": slug_enum,
                    "description": "The single best-matching project slug, or '__none__'.",
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "owner": {"type": "string", "description": "Who owns it (name), or empty."},
                            "with_person": {"type": "string", "description": "Person the task is with, or empty."},
                            "due_hint": {"type": "string", "description": "Any due/timing hint (e.g. 'Friday'), or empty."},
                        },
                        "required": ["title"],
                    },
                },
                "opportunities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "counterparty": {"type": "string", "description": "Person/company the deal is with, or empty."},
                            "stage": {
                                "type": "string",
                                "enum": ["intro", "conversations", "mou", "contract", "active"],
                            },
                            "value": {"type": "string", "description": "Money or outcome (e.g. '$20k sponsorship', '20% commission'), or empty."},
                        },
                        "required": ["title", "stage"],
                    },
                },
                "people_mentioned": {
                    "type": "array",
                    "description": "Humans worth following up with — ONLY those with a concrete reason. Omit passing mentions.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "reason": {
                                "type": "string",
                                "description": "The concrete thing to follow up on with this person. Required and specific — not 'was mentioned'.",
                            },
                        },
                        "required": ["name", "reason"],
                    },
                },
            },
            "required": ["recap", "action_items", "opportunities", "people_mentioned"],
        },
    }


def _render_prompt(
    *, title: str | None, meeting_date: str | None,
    attendees: list[dict] | list[str], summary: str,
    projects: list[dict],
) -> str:
    lines: list[str] = []
    lines.append(f"Meeting title: {title or '(untitled)'}")
    if meeting_date:
        lines.append(f"Date: {meeting_date}")
    if attendees:
        att = ", ".join(
            a.get("name") or a.get("email") or str(a) if isinstance(a, dict) else str(a)
            for a in attendees
        )
        lines.append(f"Attendees: {att}")
    lines.append("")
    lines.append("the user's projects (pick the best project_slug match):")
    for p in projects:
        lines.append(f"  - {p['slug']}: {p['name']} — {(p.get('description') or '')[:160]}")
    lines.append("")
    lines.append("Meeting recap / AI summary:")
    lines.append(summary)
    return "\n".join(lines)


async def extract_recap(
    client, model: str, *,
    title: str | None, meeting_date: str | None,
    attendees: list, summary: str, projects: list[dict],
) -> dict[str, Any] | None:
    """One forced-tool-use call. Returns the parsed extraction dict, or
    None if the model didn't call the tool. Raises on transport errors so
    the caller can surface a 502."""
    tool = _tool_schema([p["slug"] for p in projects])
    payload = _render_prompt(
        title=title, meeting_date=meeting_date, attendees=attendees,
        summary=summary, projects=projects,
    )
    resp = await client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        tools=[tool],
        tool_choice={"type": "tool", "name": "record_meeting_extraction"},
        messages=[{"role": "user", "content": payload}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_meeting_extraction":
            data = dict(block.input)
            # Normalize the '__none__' sentinel back to None.
            if data.get("project_slug") in ("__none__", "", None):
                data["project_slug"] = None
            return data
    return None


CAPTURE_SYSTEM = """You turn ONE quick note from the user — a busy crypto/Web3 \
founder & BD operator — into a single structured item. The note arrives as a \
typed message or a transcribed voice memo, so it may be terse, rambling, or \
mid-thought. Classify it as exactly ONE of:
- task: something the user (or someone) needs to DO. A concrete to-do.
- opportunity: a deal, partnership, sponsorship, intro, or revenue path.
- event: something happening at a specific time that belongs on a CALENDAR \
(a meeting, call, lunch, flight, appointment).
- expense: money the user SPENT. "spent 340 on groceries", "paid 500 baht taxi", \
"1200 rent", "bought coffee 80". A budget transaction leaving an account.
- income: money the user RECEIVED from SOMEONE ELSE. "got paid 2000", "received \
500 from client", "client sent 1500 USDT". A budget transaction arriving in an \
account from an outside party.
- transfer: money the user moves BETWEEN HIS OWN accounts (or to/from their spouse) — a top-up, withdrawal, or bank-to-bank move; NOT spending, NOT income. \
CRITICAL: on a payment slip the FROM / sender is ALMOST ALWAYS the user himself \
("the account owner", "the account owner", his own account) — that FACT ALONE IS NOT a \
transfer. It is a transfer ONLY when the RECIPIENT (the "TO" side) is ALSO the \
user's own account or his spouse: TO "the account owner" / "the spouse" / one of his own \
banks (SCB, KBank, BBL, Sber). If the TO side is a shop, company, restaurant, a \
biller (the slip shows a "Biller ID" or "Merchant ID"), or another named person → \
it is an EXPENSE (the user paid them), EVEN THOUGH the FROM line says the account owner \
Examples: FROM the account owner → TO SCB/KBank (his own) = transfer. FROM the account owner \
→ TO "บางจาก / a sport center / a shop / any Biller ID" = EXPENSE. FROM the account owner \
M. → TO "the spouse" = transfer. For a real transfer put the sending account in \
from_account_hint and the receiving own account in to_account_hint.

Decide by intent. Strong EVENT signals: the words "schedule", "book", "set up \
a call/meeting", or ANY specific clock time ("12pm", "at 3", "noon"). \
"Schedule a call with Alex at 12pm" → event. "Lunch with Alex Friday 1pm" → \
event. "Call Alex Friday" with NO clock time → task with a due date. "Maybe \
partner with CoinPost on a sponsorship" → opportunity.

Rules:
- tasks (PLURAL): when the note is a LIST of several distinct to-dos or things \
to buy ("buy apples, candies and screws", "todo: call bank, email Sam, pay rent", \
a newline/bulleted list), use type 'tasks' and put EACH item as a short clean \
title in items[] — split on commas, "and", newlines, and bullets, and drop filler \
("I need to buy", "remember to", "todo:"). A SINGLE to-do stays type 'task'. Set \
title to a short summary of the batch (e.g. "Shopping list"). Pick project_slug if \
one fits (a shopping/groceries/errands list); else '__none__'.
- title: a short, clean imperative/noun phrase. Fix obvious transcription \
noise; don't invent facts.
- project_slug: the ONE best-matching project from the provided list, or \
'__none__'.
- person_name: the main person involved (for follow-up / counterparty), or \
empty. Give the name as said — it's matched against the CRM later, so include \
the surname if spoken even if you're unsure of the spelling.
- description: any useful extra context from the note, or empty.
- TASK → due_date: resolve relative dates ("today", "tomorrow", "Friday", \
"next week") to a concrete YYYY-MM-DD using the current datetime given below. \
If a task_time is given but no date is stated ("call the bank at 3pm", "spreadsheet \
at 12:00"), default due_date to TODAY's date. Leave empty only when there is \
neither a date nor a time.
- TASK → task_time: a clock time if the to-do is pinned to one ("call the bank \
at 3pm", "post at noon") — 'HH:MM', am/pm→24h, NEVER shift for tz; empty if none. \
(A task with a time is still a to-do, NOT an event.)
- TASK → repeat: recurrence if the note implies a routine — 'daily' ("every \
day", "daily"), 'weekdays' ("every weekday", "workdays", "Mon-Fri"), 'weekly' \
("every Monday", "Tuesdays and Thursdays"), 'monthly' ("every month", "monthly"), \
'yearly' ("annually", "every year"); else 'none'.
- TASK → repeat_weekdays: when repeat='weekly', the day short-names, e.g. \
"Mondays and Wednesdays" → ["mon","wed"]; empty otherwise.
- TASK/EVENT → duration_min: how long it should take IF stated ("for 30 \
minutes", "1-hour block", "quick 15") → integer minutes; 0 if not stated.
- OPPORTUNITY → stage (intro|conversations|mou|contract|active, default intro) \
and value (e.g. '$20k sponsorship', '20% rev share'), or empty.
- EVENT → fill these atomic fields (the server assembles the datetime — do NOT \
build an ISO datetime yourself, and do NOT do ANY timezone conversion):
    · event_date: 'YYYY-MM-DD'. Resolve relative dates ("tomorrow", "Friday") \
against the current datetime below. If a time is given with no date, use the \
SOONEST future date.
    · event_time: the clock time EXACTLY as stated, only converting am/pm to \
24-hour. '12pm'→'12:00', '3pm'→'15:00', '9am'→'09:00', 'noon'→'12:00'. NEVER \
shift the hour for timezone. Empty for all-day.
    · event_end_time: '24:00'-style end time if stated, else empty.
    · tz: the IANA timezone the stated time is in. Default 'Asia/Bangkok'. Map \
named zones: 'Hong Kong'→'Asia/Hong_Kong', 'Seoul'/'Korea'→'Asia/Seoul', \
'Singapore'→'Asia/Singapore', 'EST'/'ET'/'New York'→'America/New_York', \
'PST'/'PT'→'America/Los_Angeles', 'London'/'GMT'/'BST'→'Europe/London', 'UTC'→'UTC'.
    · all_day: true for date-only events.  · location if stated.
    · conference: true if it's an ONLINE / video meeting — "Google Meet", \
"Meet", "Zoom", "video call", "jump on a call online", "virtual". false for an \
in-person meeting (lunch, office, a stated physical place).
- EXPENSE/INCOME → fill these:
    · amount: the number only (no currency symbol). "340 baht" → 340, "$50" → 50.
    · currency: the 3-letter code. Default 'THB' (the user is in Thailand). \
'$'/'dollars'/'usd' → 'USD', '฿'/'baht' → 'THB', '₽'/'rub'/'rubles' → 'RUB', \
'usdt'/'tether' → 'USDT', 'usdc' → 'USDC'.
    · category_hint: the spending/income category in plain words — 'groceries', \
'rent', 'taxi', 'salary', 'coffee', 'dining'. Empty if unclear.
    · payee: the OTHER party, NEVER the user. For money the user PAID, it's the \
recipient (the slip's "TO" side); for money the user RECEIVED, it's the sender \
(the "FROM" side). NEVER put the user's own name ("the account owner", "the owner \
the owner") as the payee — that is the user, not the counterparty. On a slip where \
FROM is the account owner and TO is a shop/biller, the SHOP is the payee. \
Thai bank slips print merchants messily — \
NORMALISE to a clean name and set category_hint to match: "TOPS"/"TOPS-C2B …" → \
payee 'Tops', category 'Groceries'. "BTM…"/"BreadTalk" → 'Bread Talk', \
'Groceries'. "Yamazaki"/"Thai Yamazaki" → 'Thai Yamazaki', 'Groceries'. \
"Grab"/"GrabFood"/"WWW.GRAB.COM" → 'Grab', 'Food Delivery' (but "GrabTaxi"/"Grab \
Rides" → category 'Taxi'). "เพ็ท คลับ…"/"Pet Club" → 'Pet Club', 'Pets'. \
"บางจาก…"/"Bangchak" (a gas-station brand) → 'Bangchak', 'Gas'. \
For a Thai merchant name you cannot read confidently, KEEP THE THAI (or a faithful \
transliteration) — do NOT invent or translate into English words that are not on \
the slip (never turn a name into 'School', 'Education', etc.); if unsure, leave \
category_hint empty rather than guessing.
    · account_hint: which account/wallet the money moved through IF stated — \
'cash', 'KBank', 'Sber', 'SCB', 'crypto'. Empty if not mentioned. (EXPENSE = the \
account it left; INCOME = the account it arrived in.)
    · title: a short label like 'Groceries at Tops' or 'Taxi'.
- TRANSFER → fill amount, currency, txn_date as above, then:
    · from_account_hint: the SENDING account (where the money left) if the slip \
names it — 'KBank', 'Sber', a bank the sender used. Empty if not shown.
    · to_account_hint: the RECEIVING account (where the money arrived). If the \
recipient (the "TO" side) is a PERSON — e.g. your spouse "the spouse" — use the \
RECIPIENT'S NAME here ('spouse'); their account is named after them. Do NOT use \
the sending app's bank (the SCB app you paid FROM) as the destination — that is \
the SOURCE (from_account_hint). NEVER set from_account_hint and to_account_hint \
to the same bank. Empty if truly unknown. Leave payee/category_hint empty for \
transfers.
Only fill the block for the chosen type; leave the others empty."""


def _capture_tool(project_slugs: list[str]) -> dict[str, Any]:
    slug_enum = project_slugs + ["__none__"]
    return {
        "name": "record_capture",
        "description": "Record the one structured item this note becomes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["task", "tasks", "opportunity", "event", "expense", "income", "transfer"]},
                "title": {"type": "string"},
                "description": {"type": "string", "description": "Extra context, or empty."},
                "items": {"type": "array", "items": {"type": "string"},
                          "description": "TASKS (plural): each individual to-do / shopping item as a short title, e.g. ['Apples','Candies','Screws']. Empty for non-'tasks' types."},
                "project_slug": {"type": "string", "enum": slug_enum},
                "person_name": {"type": "string", "description": "Main person involved, or empty."},
                # task
                "due_date": {"type": "string", "description": "TASK: YYYY-MM-DD or empty."},
                "task_time": {"type": "string", "description": "TASK: clock time 'HH:MM' (am/pm→24h, no tz shift) if the to-do is at a specific time; empty otherwise."},
                "repeat": {"type": "string", "enum": ["none", "daily", "weekdays", "weekly", "monthly", "yearly"], "description": "TASK: recurrence ('every day'→daily, 'every weekday'→weekdays, 'every Monday'→weekly, monthly, yearly); else none."},
                "repeat_weekdays": {"type": "array", "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}, "description": "TASK: days when repeat=weekly, e.g. ['mon','wed']."},
                "duration_min": {"type": "integer", "description": "TASK/EVENT: minutes it should take if stated ('for 30 min'→30); 0 if not."},
                # opportunity
                "stage": {
                    "type": "string",
                    "enum": ["intro", "conversations", "mou", "contract", "active"],
                    "description": "OPPORTUNITY stage.",
                },
                "value": {"type": "string", "description": "OPPORTUNITY: money/outcome, or empty."},
                # event — atomic fields; the SERVER assembles the datetime
                "tz": {"type": "string", "description": "EVENT: IANA timezone the stated time is in (e.g. 'Asia/Seoul'). Default 'Asia/Bangkok'."},
                "event_date": {"type": "string", "description": "EVENT: 'YYYY-MM-DD' (resolve relative dates)."},
                "event_time": {"type": "string", "description": "EVENT: clock time as stated, am/pm→24h only ('12pm'→'12:00'). NEVER shift for tz. Empty for all-day."},
                "event_end_time": {"type": "string", "description": "EVENT: end clock time 'HH:MM', or empty."},
                "all_day": {"type": "boolean", "description": "EVENT: true for date-only events."},
                "location": {"type": "string", "description": "EVENT: physical location, or empty."},
                "conference": {"type": "boolean", "description": "EVENT: true for an online/video meeting (Google Meet, Zoom, video call); false for in-person."},
                # expense / income
                "amount": {"type": "number", "description": "EXPENSE/INCOME: the number only, no symbol. 0 if none."},
                "currency": {"type": "string", "description": "EXPENSE/INCOME: 3-letter code, default 'THB'. $→USD, ฿→THB, ₽→RUB, usdt→USDT."},
                "category_hint": {"type": "string", "description": "EXPENSE/INCOME: category in plain words ('groceries','rent','salary'), or empty."},
                "payee": {"type": "string", "description": "EXPENSE/INCOME: merchant or who paid/was paid, or empty."},
                "account_hint": {"type": "string", "description": "EXPENSE/INCOME: account/wallet if stated ('cash','KBank','Sber'), or empty."},
                "from_account_hint": {"type": "string", "description": "TRANSFER: the SENDING account (money left it) if the slip names it, or empty."},
                "to_account_hint": {"type": "string", "description": "TRANSFER: the RECEIVING account (money arrived in it) if the slip names it, or empty."},
                "txn_date": {"type": "string", "description": "EXPENSE/INCOME/TRANSFER: the transaction date as YYYY-MM-DD if shown (e.g. on a receipt/slip); empty to default to today."},
            },
            "required": ["type", "title"],
        },
    }


async def classify_capture(
    client, model: str, *,
    text: str, now_iso: str, tz_name: str, projects: list[dict],
    force_type: str | None = None, self_label: str = "the user",
    image_b64: str | None = None, image_media_type: str = "image/jpeg",
) -> dict[str, Any] | None:
    """One forced-tool-use call. Classifies a quick note into a task /
    opportunity / event and extracts its fields. Returns the parsed dict (with
    project_slug normalized; '__none__'→None), or None if the model didn't call
    the tool. `force_type` pins the classification (the bot's 🔀 Change-type).
    When `image_b64` is set (a payment-slip photo), the model reads the image and
    extracts the transaction — Thai SCB/KBank/PromptPay slips parse cleanly."""
    tool = _capture_tool([p["slug"] for p in projects])
    lines = [f"Current datetime: {now_iso} ({tz_name}). Resolve all relative dates/times against this."]
    # The system prompt says "the user" throughout (kept generic on purpose). Give
    # the model their REAL name for anything that lands in output — otherwise it
    # titles meetings literally "the user <> Counterparty".
    lines.append(
        f'The user\'s name is "{self_label}". Whenever a title or field needs their '
        f'name, write "{self_label}" — e.g. a 1:1 meeting title "{self_label} <> '
        f'Counterparty". NEVER write the literal words "the user" as their name.'
    )
    lines.append("")
    lines.append("the user's projects (pick the best project_slug, or '__none__'):")
    for p in projects:
        lines.append(f"  - {p['slug']}: {p['name']} — {(p.get('description') or '')[:160]}")
    lines.append("")
    if force_type:
        lines.append(f"Classify this note as a {force_type} (type must be '{force_type}').")
    if image_b64:
        lines.append(
            "The attached image is a bank payment slip / receipt (often Thai — SCB, "
            "KBank, PromptPay). Read it and record the transaction: amount, currency "
            "(THB unless shown otherwise), the OTHER party as the payee, and use the "
            "slip's date as the transaction date. Classify as 'expense' when money "
            "left the user's account (FROM the user → he paid) or 'income' when money came "
            "IN (TO the user). Any text below is the user's caption — use it as a hint.")
    lines.append("Note:")
    lines.append(text or "(receipt image — no caption)")
    user_content: Any = "\n".join(lines)
    if image_b64:
        user_content = [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": image_media_type, "data": image_b64}},
            {"type": "text", "text": "\n".join(lines)},
        ]
    resp = await client.messages.create(
        model=model,
        max_tokens=1000,
        system=CAPTURE_SYSTEM,
        tools=[tool],
        tool_choice={"type": "tool", "name": "record_capture"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_capture":
            data = dict(block.input)
            if data.get("project_slug") in ("__none__", "", None):
                data["project_slug"] = None
            if force_type:
                data["type"] = force_type
            return data
    return None


DECOMPOSE_SYSTEM = """You break a single task into a short, ordered checklist \
of concrete subtasks for a busy crypto/Web3 founder/BD operator (the user). \
Output 2-6 subtasks — the natural steps to get this done, in execution order. \
Each is a short imperative action ("Draft the X", "Send to Y for review"). \
Be specific to the task, not generic project-management filler. If the task \
is already atomic (can't sensibly be split), return fewer items or an empty \
list. Never pad to hit a number."""

DECOMPOSE_TOOL = {
    "name": "record_subtasks",
    "description": "Record the ordered subtasks the task breaks down into.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": {"type": "string", "description": "A short imperative subtask title."},
            },
        },
        "required": ["subtasks"],
    },
}


DRAFT_SYSTEM = """You draft a short outreach message that the user — a busy \
crypto/Web3 founder & BD operator — will review before sending. Write in HIS \
voice: direct, warm but efficient, no corporate fluff, no over-apologizing. \
Match the channel: Telegram = casual, 1-4 short sentences, no salutation \
needed; Email = a brief subject line + 2-5 sentences with a light greeting \
and sign-off. Sign off with the sender's name exactly as given by the "You are:" \
line in the prompt (e.g. "Best, <that name>") — never the literal words "the user".

Ground the message in the recent conversation and the open item (task or \
opportunity) you're given — reference the actual context, propose a concrete \
next step, and make it easy to reply. Do NOT invent facts, prices, or \
commitments not present in the context. NEVER fabricate URLs, scheduling/ \
calendar links, phone numbers, or handles — if one would help, leave a clear \
placeholder like "[your scheduling link]" for the user to fill in. Write in the \
same language as the recent conversation. If there's barely any context, \
write a brief, genuine check-in rather than padding.

This is a DRAFT for review — never assume it will be sent as-is."""

DRAFT_TOOL = {
    "name": "record_draft",
    "description": "Record the drafted outreach message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Email subject line (email channel only; empty for telegram)."},
            "body": {"type": "string", "description": "The message body."},
        },
        "required": ["body"],
    },
}


def build_draft_prompt(
    *, person_name: str, channel: str, self_label: str,
    recent_messages: list[dict], context_label: str | None,
) -> str:
    """The user prompt for a draft — shared by the cloud and local paths
    (pure, so the sensitive route provably sends the SAME context)."""
    lines = [f"Draft a {channel} message to: {person_name}", f"You are: {self_label}"]
    if context_label:
        lines.append(f"Open item to follow up on: {context_label}")
    lines.append("")
    if recent_messages:
        lines.append("Recent conversation (chronological):")
        for m in recent_messages:
            who = self_label if m.get("direction") == "outbound" else person_name
            body = (m.get("body") or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:299] + "…"
            if body:
                lines.append(f"  [{who}] {body}")
    else:
        lines.append("(No recent messages — write a brief, genuine check-in.)")
    lines.append("")
    lines.append(f"Write the {channel} draft now.")
    return "\n".join(lines)


def parse_local_draft(text: str, channel: str) -> dict:
    """Parse the local model's draft reply. JSON {subject, body} first;
    otherwise the whole text is the body (with a leading `Subject: …` line
    split out for email). Telegram never gets a subject. Pure — tested."""
    subj: str | None = None
    body = (text or "").strip()
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            subj = (data.get("subject") or "").strip() or None
            body = (data.get("body") or "").strip()
    except (ValueError, TypeError):
        first, _, rest = body.partition("\n")
        if first.lower().startswith("subject:"):
            subj = first[len("subject:"):].strip() or None
            body = rest.strip()
    return {"subject": subj if channel == "email" else None, "body": body}


async def draft_outreach_local(
    *, ollama_url: str, ollama_model: str,
    person_name: str, channel: str, self_label: str,
    recent_messages: list[dict], context_label: str | None,
) -> dict:
    """Sensitive-contact draft: same system prompt and user prompt, on-box
    Ollama, JSON asked for in-prompt. Raises on failure — the caller surfaces
    the error; it must NEVER fall back to the cloud path."""
    import httpx

    prompt = build_draft_prompt(
        person_name=person_name, channel=channel, self_label=self_label,
        recent_messages=recent_messages, context_label=context_label)
    system = DRAFT_SYSTEM + (
        '\n\nReply ONLY with JSON: {"subject": "...", "body": "..."} '
        "(subject empty unless this is an email)."
    )
    # 240s: a cold Ollama load (~keep_alive expired) + a 4k-char prefill on a
    # busy 4-vCPU box can exceed 120s; the UI fetch has no timeout of its own.
    async with httpx.AsyncClient(timeout=240) as hc:
        resp = await hc.post(
            f"{ollama_url.rstrip('/')}/api/chat",
            json={
                "model": ollama_model, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                "options": {"temperature": 0.3, "num_predict": 600},
            })
        resp.raise_for_status()
        text = ((resp.json().get("message") or {}).get("content") or "")
    return parse_local_draft(text, channel)


async def draft_outreach(
    client, model: str, *,
    person_name: str, channel: str, self_label: str,
    recent_messages: list[dict], context_label: str | None,
) -> dict:
    """Draft a follow-up. Returns {subject?, body}. recent_messages is a list
    of {direction, body} newest-last."""
    resp = await client.messages.create(
        model=model,
        max_tokens=600,
        system=DRAFT_SYSTEM,
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": "record_draft"},
        messages=[{"role": "user", "content": build_draft_prompt(
            person_name=person_name, channel=channel, self_label=self_label,
            recent_messages=recent_messages, context_label=context_label)}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_draft":
            d = dict(block.input)
            subj = (d.get("subject") or "").strip() or None
            return {"subject": subj if channel == "email" else None, "body": (d.get("body") or "").strip()}
    return {"subject": None, "body": ""}


async def decompose_task(
    client, model: str, *,
    title: str, description: str | None = None, project_name: str | None = None,
) -> list[str]:
    """Propose subtasks for a task (propose-then-confirm — the caller does
    NOT auto-create them). Returns an ordered list of titles, possibly empty."""
    lines = [f"Task: {title}"]
    if description:
        lines.append(f"Details: {description}")
    if project_name:
        lines.append(f"Project: {project_name}")
    lines.append("")
    lines.append("Break this into its concrete subtasks.")
    resp = await client.messages.create(
        model=model,
        max_tokens=800,
        system=DECOMPOSE_SYSTEM,
        tools=[DECOMPOSE_TOOL],
        tool_choice={"type": "tool", "name": "record_subtasks"},
        messages=[{"role": "user", "content": "\n".join(lines)}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_subtasks":
            out = []
            for s in (dict(block.input).get("subtasks") or []):
                s = (s or "").strip()
                if s:
                    out.append(s)
            return out
    return []
