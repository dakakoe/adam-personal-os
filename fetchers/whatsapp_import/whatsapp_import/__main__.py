"""One-shot import of WhatsApp history from an iPhone local backup.

Runs on the Mac, not the droplet — the backup is here. Three subcommands:

  inspect   what the backup and its message store look like. Run this first
            on an unfamiliar WhatsApp version; it changes nothing.
  extract   backup -> an NDJSON file. Touches no database, so the result can
            be looked at before anything is written.
  load      NDJSON (or a fresh extract) -> raw.whatsapp_*.

Split that way on purpose: extract is inspectable and re-runnable, and load is
idempotent, so a failed run is never half an import you cannot reason about.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import csv
import os
import re
import sqlite3
import sys
from pathlib import Path

from . import backup as backup_mod
from . import chatexport, chatstorage, db
from .config import load as load_config

log = logging.getLogger("whatsapp_import")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _open(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _resolve_backup(args) -> backup_mod.BackupInfo:
    info = backup_mod.pick_backup(Path(args.backup_dir) if args.backup_dir else None)
    backup_mod.require_usable(info)
    log.info(
        "backup: %s (device=%s, last=%s)",
        info.path.name, info.device_name, info.last_backup,
    )
    return info


def cmd_inspect(args) -> int:
    try:
        backups = backup_mod.list_backups()
    except backup_mod.BackupError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Backups found: {len(backups)}")
    for b in backups:
        flag = "ENCRYPTED — unusable" if b.encrypted else "ok"
        print(f"  {b.path.name}  device={b.device_name}  last={b.last_backup}  [{flag}]")

    info = _resolve_backup(args)
    with backup_mod.StagedDatabase(info.path) as sqlite_path:
        con = _open(sqlite_path)
        try:
            report = chatstorage.inspect(con)
        finally:
            con.close()
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_extract(args) -> int:
    info = _resolve_backup(args)
    out = Path(args.out).expanduser()
    counts = {"messages": 0, "contacts": 0, "groups": 0, "skipped_groups": 0}

    with backup_mod.StagedDatabase(info.path) as sqlite_path:
        con = _open(sqlite_path)
        try:
            # Written as one object per line so a huge archive streams rather
            # than being held in memory at either end.
            with out.open("w", encoding="utf-8") as fh:
                os.chmod(out, 0o600)
                for c in chatstorage.contacts_from(con, self_jid=args.self_jid):
                    fh.write(json.dumps({"t": "contact", **c}, default=str) + "\n")
                    counts["contacts"] += 1
                for g in chatstorage.groups_from(con):
                    fh.write(json.dumps({"t": "group", **g}, default=str) + "\n")
                    counts["groups"] += 1
                for m in chatstorage.iter_messages(
                    con, self_jid=args.self_jid, only_chat=args.only_chat
                ):
                    fh.write(json.dumps({"t": "message", **m}, default=str) + "\n")
                    counts["messages"] += 1
        except LookupError as e:
            print(f"Cannot read this store: {e}", file=sys.stderr)
            print("Run `inspect` and send me the output.", file=sys.stderr)
            return 3
        finally:
            con.close()

    log.info("extracted %s -> %s", counts, out)
    print(json.dumps(counts, indent=2))
    return 0


async def _load(path: Path, args) -> int:
    cfg = load_config()
    pool = await db.connect(cfg.db_url)
    try:
        contacts, groups, messages = [], [], []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                kind = row.pop("t", None)
                (contacts if kind == "contact" else
                 groups if kind == "group" else
                 messages if kind == "message" else []).append(row)

        n_groups = await db.load_groups(pool, groups)
        n_contacts = await db.load_contacts(pool, contacts)

        # Groups obey the same opt-in rule as the live bridge, so history and
        # live never disagree about which groups are in the corpus. Enabling a
        # group later means re-running with --only-chat, not a full re-import.
        enabled = await db.enabled_groups(pool)
        kept, skipped = [], 0
        for m in messages:
            if m.get("is_group") and m["chat_jid"] not in enabled:
                skipped += 1
                continue
            kept.append(m)

        staged, inserted = await db.load_messages(pool, kept)

        print(json.dumps({
            "contacts": n_contacts,
            "groups_recorded": n_groups,
            "messages_in_file": len(messages),
            "messages_skipped_group_not_enabled": skipped,
            "messages_staged": staged,
            "rows_actually_new": inserted,
        }, indent=2))
        if args.verbose_note and inserted == 0 and staged:
            print("\n0 new rows: everything in this file was already imported.", file=sys.stderr)
        return 0
    finally:
        await pool.close()


def cmd_load(args) -> int:
    path = Path(args.infile).expanduser()
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 2
    return asyncio.run(_load(path, args))


async def _import_export(paths: list[Path], args) -> int:
    cfg = load_config()
    pool = await db.connect(cfg.db_url)
    try:
        chat_key = args.chat.strip().lstrip("+")
        chat_jid = (
            chat_key if chat_key.endswith(("@g.us", "@lid", "@s.whatsapp.net"))
            else f"{chat_key}@s.whatsapp.net"
        )
        is_group = chat_jid.endswith("@g.us")
        key = chat_jid if is_group else chat_key

        rows: list[dict] = []
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            n = 0
            for m in chatexport.parse(
                text, chat_key=key, chat_jid=chat_jid,
                self_name=args.self_name, is_group=is_group,
            ):
                rows.append(m)
                n += 1
            log.info("%s -> %d messages", path.name, n)

        if not rows:
            print("No messages parsed. Is this a WhatsApp chat export?", file=sys.stderr)
            return 3

        if not is_group and args.name:
            await db.load_contacts(pool, [{
                "jid": chat_jid,
                "phone_e164": chat_key if chat_key.isdigit() else None,
                "lid": chat_jid if chat_jid.endswith("@lid") else None,
                # An export gives a display name, which is worth keeping, but
                # only as the self-declared kind — it is not the address book.
                "push_name": args.name,
                "notify_name": None,
            }])

        staged, skipped, new = await db.load_export_messages(pool, rows)
        print(json.dumps({
            "chat": chat_jid,
            "messages_parsed": len(rows),
            "skipped_overlapping_live": skipped,
            "staged": staged,
            "rows_actually_new": new,
        }, indent=2))
        return 0
    finally:
        await pool.close()


def cmd_export_file(args) -> int:
    paths: list[Path] = []
    for raw in args.files:
        p = Path(raw).expanduser()
        if p.is_dir():
            paths.extend(sorted(p.glob("*.txt")))
        elif p.exists():
            paths.append(p)
        else:
            print(f"No such file: {p}", file=sys.stderr)
            return 2
    if not paths:
        print("No .txt exports found.", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_import_export(paths, args))
    except ValueError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 3


# "WhatsApp Chat with Alice.txt" -> "Alice". Localised variants keep the
# name after the last " with ", which holds across the languages I have seen.
_FNAME = re.compile(r"(?:chat\s+with\s+)(?P<name>.+?)$", re.I)


def chat_name_from_filename(stem: str) -> str:
    m = _FNAME.search(stem.strip())
    return (m.group("name") if m else stem).strip()


def _exports_in(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(p.glob("*.txt")))
        elif p.exists():
            out.append(p)
    return out


async def _scan(paths: list[Path], out_csv: Path) -> int:
    cfg = load_config()
    pool = await db.connect(cfg.db_url)
    try:
        names = [chat_name_from_filename(p.stem) for p in paths]
        suggestions = await db.suggest_numbers(pool, names)

        with out_csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["file", "chat_name", "phone_or_jid", "note"])
            filled = 0
            for path, name in zip(paths, names):
                cands = suggestions.get(name.lower(), [])
                if len(cands) == 1:
                    phone, note = cands[0]["phone"], f"matched {cands[0]['display_name']}"
                    filled += 1
                elif len(cands) > 1:
                    phone = ""
                    note = "AMBIGUOUS: " + ", ".join(c["phone"] for c in cands[:4])
                else:
                    phone, note = "", "no match — fill in the number"
                w.writerow([path.name, name, phone, note])

        print(json.dumps({
            "exports_found": len(paths),
            "pre_filled": filled,
            "needs_你_to_fill": len(paths) - filled,
            "map_file": str(out_csv),
        }, indent=2).replace("needs_你_to_fill", "needs_you_to_fill"))
        print(f"\nOpen {out_csv}, fill any blank phone_or_jid, then run `chat-export --map`.",
              file=sys.stderr)
        return 0
    finally:
        await pool.close()


def cmd_scan(args) -> int:
    paths = _exports_in(args.files)
    if not paths:
        print("No .txt exports found.", file=sys.stderr)
        return 2
    return asyncio.run(_scan(paths, Path(args.out).expanduser()))


async def _import_many(pairs: list[tuple[Path, str]], args) -> int:
    """Import several exports, each with its own chat, in one pass."""
    cfg = load_config()
    pool = await db.connect(cfg.db_url)
    totals = {"files": 0, "parsed": 0, "skipped_overlapping_live": 0, "new": 0, "failed": []}
    try:
        for path, chat in pairs:
            chat_key = chat.strip().lstrip("+")
            chat_jid = (
                chat_key if chat_key.endswith(("@g.us", "@lid", "@s.whatsapp.net"))
                else f"{chat_key}@s.whatsapp.net"
            )
            is_group = chat_jid.endswith("@g.us")
            key = chat_jid if is_group else chat_key
            try:
                rows = list(chatexport.parse(
                    path.read_text(encoding="utf-8", errors="replace"),
                    chat_key=key, chat_jid=chat_jid,
                    self_name=args.self_name, is_group=is_group,
                ))
            except ValueError as e:
                # One unreadable file must not abandon the rest of the batch.
                log.error("%s: %s", path.name, e)
                totals["failed"].append({"file": path.name, "error": str(e)})
                continue
            if not rows:
                totals["failed"].append({"file": path.name, "error": "no messages parsed"})
                continue

            if not is_group:
                await db.load_contacts(pool, [{
                    "jid": chat_jid,
                    "phone_e164": chat_key if chat_key.isdigit() else None,
                    "lid": chat_jid if chat_jid.endswith("@lid") else None,
                    "push_name": chat_name_from_filename(path.stem),
                    "notify_name": None,
                }])

            staged, skipped, new = await db.load_export_messages(pool, rows)
            totals["files"] += 1
            totals["parsed"] += len(rows)
            totals["skipped_overlapping_live"] += skipped
            totals["new"] += new
            log.info("%s -> parsed=%d new=%d", path.name, len(rows), new)

        print(json.dumps(totals, indent=2))
        return 0 if not totals["failed"] else 1
    finally:
        await pool.close()


def cmd_export_map(args) -> int:
    """Import a whole folder using the CSV produced by `scan`."""
    mp = Path(args.map).expanduser()
    if not mp.exists():
        print(f"No such map file: {mp}", file=sys.stderr)
        return 2
    base = Path(args.dir).expanduser() if args.dir else mp.parent
    pairs: list[tuple[Path, str]] = []
    missing: list[str] = []
    with mp.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            chat = (row.get("phone_or_jid") or "").strip()
            fname = (row.get("file") or "").strip()
            if not fname:
                continue
            if not chat:
                missing.append(fname)
                continue
            path = base / fname
            if not path.exists():
                missing.append(fname)
                continue
            pairs.append((path, chat))
    if missing:
        print(f"Skipping {len(missing)} row(s) with no number or no file:", file=sys.stderr)
        for m in missing[:10]:
            print(f"  {m}", file=sys.stderr)
    if not pairs:
        print("Nothing to import — fill in phone_or_jid in the map file.", file=sys.stderr)
        return 2
    return asyncio.run(_import_many(pairs, args))


def main() -> int:
    _setup_logging()
    os.umask(0o077)  # the extract holds private message content

    p = argparse.ArgumentParser(prog="whatsapp_import", description=__doc__)
    p.add_argument("--backup-dir", help="a specific backup folder (default: newest)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="show backups and the store's shape; changes nothing")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("extract", help="backup -> NDJSON, no database involved")
    s.add_argument("--out", required=True)
    s.add_argument("--self-jid", help="your own jid, e.g. 15551234567@s.whatsapp.net")
    s.add_argument("--only-chat", help="limit to one chat jid")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser(
        "chat-export",
        help="import WhatsApp's own 'Export Chat' .txt files (no backup needed)",
    )
    s.add_argument("files", nargs="+", help="one or more .txt exports, or a folder")
    s.add_argument("--chat", required=True,
                   help="whose chat this is: phone digits, or a full @g.us jid for a group")
    s.add_argument("--self-name", required=True,
                   help="your own name EXACTLY as it appears in the export")
    s.add_argument("--name", help="the other person's name, recorded as their WhatsApp name")
    s.set_defaults(func=cmd_export_file)

    s = sub.add_parser(
        "scan",
        help="look at a folder of exports and write a map file, pre-filled from contacts you already have",
    )
    s.add_argument("files", nargs="+", help="folder of .txt exports (or individual files)")
    s.add_argument("--out", default="wa-map.csv")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser(
        "chat-export-batch",
        help="import a whole folder of exports using the map file from `scan`",
    )
    s.add_argument("--map", required=True, help="the CSV written by `scan`, with numbers filled in")
    s.add_argument("--dir", help="folder holding the .txt files (default: the map file's folder)")
    s.add_argument("--self-name", required=True,
                   help="your own name EXACTLY as it appears in the exports")
    s.set_defaults(func=cmd_export_map)

    s = sub.add_parser("load", help="NDJSON -> raw.whatsapp_*; idempotent")
    s.add_argument("--in", dest="infile", required=True)
    s.add_argument("--verbose-note", action="store_true", default=True)
    s.set_defaults(func=cmd_load)

    args = p.parse_args()
    try:
        return args.func(args)
    except backup_mod.BackupError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
