"""LinkedIn Data Export importer.

Reads the ZIP that LinkedIn emails you 24-72h after requesting an archive,
parses Connections.csv and messages.csv into raw.linkedin_*. Idempotent —
re-running on a newer export adds only new rows."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg


log = logging.getLogger(__name__)


# Connections.csv has a multi-line preamble in older exports ("Notes:"…). We
# detect the header row by looking for "First Name".
def _read_csv_after_header(file_bytes: bytes, marker: str) -> list[dict[str, str]]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if marker in line:
            start = i
            break
    reader = csv.DictReader(lines[start:])
    return [dict(row) for row in reader]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    # LinkedIn ships several formats across years
    fmts = (
        "%d %b %Y, %H:%M",         # messages.csv
        "%d %b %Y",                # connections.csv "Connected On"
        "%Y-%m-%d %H:%M:%S UTC",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(";") if s.strip()]


async def _import_connections(pool: asyncpg.Pool, rows: list[dict[str, str]]) -> int:
    if not rows:
        return 0
    payload = []
    for r in rows:
        url = (r.get("URL") or "").strip()
        if not url:
            continue
        connected = _parse_date(r.get("Connected On"))
        payload.append((
            (r.get("First Name") or "").strip() or None,
            (r.get("Last Name") or "").strip() or None,
            ((r.get("Email Address") or "").strip().lower() or None),
            (r.get("Company") or "").strip() or None,
            (r.get("Position") or "").strip() or None,
            url,
            connected.date() if connected else None,
        ))
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.linkedin_connection
              (first_name, last_name, email, company, position, url, connected_on)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (url) DO UPDATE SET
              first_name = COALESCE(EXCLUDED.first_name, raw.linkedin_connection.first_name),
              last_name  = COALESCE(EXCLUDED.last_name,  raw.linkedin_connection.last_name),
              email      = COALESCE(EXCLUDED.email,      raw.linkedin_connection.email),
              company    = COALESCE(EXCLUDED.company,    raw.linkedin_connection.company),
              position   = COALESCE(EXCLUDED.position,   raw.linkedin_connection.position),
              connected_on = COALESCE(EXCLUDED.connected_on, raw.linkedin_connection.connected_on)
            """,
            payload,
        )
    return len(payload)


def _split_csv_list(value: str | None) -> list[str]:
    """ImportedContacts uses comma- or space-separated emails/phones in a
    single CSV cell. Split on any of ',', ';', or multiple whitespace."""
    if not value:
        return []
    import re
    parts = re.split(r"[,;]|\s{2,}", value)
    return [p.strip() for p in parts if p.strip()]


def _parse_imported_date(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    # ImportedContacts uses 'M/D/YY, H:MM AM' format
    fmts = ("%m/%d/%y, %I:%M %p", "%m/%d/%Y, %I:%M %p", "%Y-%m-%d %H:%M:%S")
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


async def _import_imported_contacts(pool: asyncpg.Pool, rows: list[dict[str, str]]) -> int:
    """ImportedContacts.csv: address-book entries the user uploaded to
    LinkedIn at some point. Snapshot-style — we TRUNCATE before reload so
    the table mirrors the most recent export exactly."""
    if not rows:
        return 0
    payload = []
    for r in rows:
        emails = [e.lower() for e in _split_csv_list(r.get("Emails"))]
        phones = _split_csv_list(r.get("PhoneNumbers"))
        payload.append((
            (r.get("FirstName") or "").strip() or None,
            (r.get("MiddleName") or "").strip() or None,
            (r.get("LastName") or "").strip() or None,
            (r.get("NickName") or "").strip() or None,
            (r.get("NamePrefix") or "").strip() or None,
            (r.get("NameSuffix") or "").strip() or None,
            (r.get("Title") or "").strip() or None,
            emails,
            phones,
            (r.get("CountryCode") or "").strip() or None,
            (r.get("RegionCode") or "").strip() or None,
            (r.get("Location") or "").strip() or None,
            _parse_imported_date(r.get("CreatedAt")),
            _parse_imported_date(r.get("UpdatedAt")),
        ))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("TRUNCATE raw.linkedin_imported_contact")
            await conn.executemany(
                """
                INSERT INTO raw.linkedin_imported_contact
                  (first_name, middle_name, last_name, nickname, prefix, suffix,
                   title, emails, phones, country_code, region_code, location,
                   source_created_at, source_updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                payload,
            )
    return len(payload)


async def _import_messages(pool: asyncpg.Pool, rows: list[dict[str, str]]) -> int:
    if not rows:
        return 0
    payload = []
    for r in rows:
        # Schema varies slightly across exports; handle both old and new headers.
        date = _parse_date(r.get("DATE") or r.get("Date"))
        if date is None:
            continue
        from_url = (r.get("SENDER PROFILE URL") or r.get("Sender Profile URL") or "").strip() or None
        payload.append((
            (r.get("CONVERSATION ID") or r.get("Conversation ID") or "").strip() or None,
            (r.get("CONVERSATION TITLE") or r.get("Conversation Title") or "").strip() or None,
            (r.get("FROM") or r.get("From") or "").strip() or None,
            from_url,
            _split_list(r.get("TO") or r.get("To")),
            _split_list(r.get("RECIPIENT PROFILE URLS") or r.get("Recipient Profile URLs")),
            date,
            (r.get("SUBJECT") or r.get("Subject") or "").strip() or None,
            (r.get("CONTENT") or r.get("Content") or "").strip() or None,
            (r.get("FOLDER") or r.get("Folder") or "").strip() or None,
        ))
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO raw.linkedin_message
              (conversation_id, conversation_title, from_name, from_profile_url,
               to_names, to_profile_urls, message_date, subject, content, folder)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (conversation_id, message_date, from_profile_url) DO NOTHING
            """,
            payload,
        )
    return len(payload)


def _build_db_url() -> str:
    user = os.environ["POSTGRES_USER"]
    pw = os.environ["POSTGRES_PASSWORD"]
    db = os.environ["POSTGRES_DB"]
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgres://{user}:{pw}@{host}:{port}/{db}"


async def run(zip_path: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db_url = os.environ.get("LINKEDIN_IMPORT_DATABASE_URL") or _build_db_url()
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, statement_cache_size=0)

    try:
        log.info("opening %s", zip_path)
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            log.info("zip has %d files", len(names))

            # LinkedIn names files differently across years; case-insensitive
            # match on the basename handles "Connections.csv" / "connections.csv".
            # Earlier we used endswith() which matched too liberally
            # ("learning_role_play_messages.csv" satisfied "messages.csv");
            # require an exact basename match instead.
            def find_one(target: str) -> str | None:
                t = target.lower()
                for n in names:
                    basename = n.rsplit("/", 1)[-1].lower()
                    if basename == t:
                        return n
                return None

            conn_name = find_one("connections.csv")
            msg_name = find_one("messages.csv")
            imported_name = find_one("importedcontacts.csv")

            n_conn = n_msg = n_imported = 0
            if conn_name:
                log.info("connections: %s", conn_name)
                rows = _read_csv_after_header(z.read(conn_name), "First Name")
                log.info("connections: %d rows in csv", len(rows))
                n_conn = await _import_connections(pool, rows)
                log.info("connections: %d rows attempted (idempotent upsert)", n_conn)
            else:
                log.warning("no Connections.csv in zip")

            if msg_name:
                log.info("messages: %s", msg_name)
                rows = _read_csv_after_header(z.read(msg_name), "CONVERSATION ID")
                if not rows:
                    rows = _read_csv_after_header(z.read(msg_name), "Conversation ID")
                log.info("messages: %d rows in csv", len(rows))
                n_msg = await _import_messages(pool, rows)
                log.info("messages: %d rows attempted (idempotent insert)", n_msg)
            else:
                log.warning("no messages.csv in zip")

            if imported_name:
                log.info("imported contacts: %s", imported_name)
                rows = _read_csv_after_header(z.read(imported_name), "FirstName")
                log.info("imported contacts: %d rows in csv", len(rows))
                n_imported = await _import_imported_contacts(pool, rows)
                log.info("imported contacts: %d rows loaded (TRUNCATE + insert)", n_imported)
            else:
                log.warning("no ImportedContacts.csv in zip")

            log.info("linkedin import done: connections=%d messages=%d imported=%d",
                     n_conn, n_msg, n_imported)
            # Stable stdout marker for the setup wizard (logs go to stderr) —
            # parsed by merge_api setup_flow.parse_linkedin_output.
            print(f"linkedin_import_result connections={n_conn} messages={n_msg} "
                  f"imported={n_imported}", flush=True)
            return 0
    finally:
        await pool.close()


def main() -> int:
    p = argparse.ArgumentParser(prog="linkedin_import")
    p.add_argument("zip_path", help="Path to the LinkedIn data export ZIP")
    args = p.parse_args()
    return asyncio.run(run(args.zip_path))


if __name__ == "__main__":
    raise SystemExit(main())
