# WhatsApp history import

A linked device receives almost no history, so the archive has to come from
somewhere else. Two routes, both writing into the same tables the live bridge
writes:

| | Needs | Gives you |
|---|---|---|
| **iPhone backup** | 50-150 GB of free disk | Everything, all chats at once, with address-book names |
| **Chat export** | Almost no disk | One conversation at a time, no phone numbers, no real names |

Use the backup if it fits. If it does not, `chat-export` is the fallback and
needs nothing but a few megabytes.

**This runs on the Mac, not the droplet** — that is where the backup is. It is
the only fetcher with no systemd unit, and it is a one-shot, not a service.

It never touches WhatsApp or the network. It reads a file that is already on
this machine.

## What you have to do first

**1. Take an unencrypted backup.** Connect the iPhone, open Finder, select the
device, choose "Back up all of the data on your iPhone to this Mac", make sure
**Encrypt local backup is UNCHECKED**, then Back Up Now.

Encryption cannot be removed from a backup that already exists, so if yours is
encrypted you need a fresh one. Worth knowing the tradeoff: an unencrypted
backup sitting on this Mac is readable by anything with disk access. Consider
turning encryption back on once the import is done.

**2. Grant Full Disk Access.** The backup folder is protected by macOS privacy
controls, so without this every read fails with "Operation not permitted",
which looks like a missing backup and is not.

System Settings → Privacy & Security → Full Disk Access → enable it for the
app you are running this from (Terminal, iTerm, or Claude), then **restart that
app**.

**3. Pair the live bridge first, then take the backup.** In that order. The
backup is a snapshot and live ingest starts at pairing, so doing it the other
way round loses everything in between.

## Usage

Three subcommands, and they are separate on purpose: `extract` produces a file
you can look at before anything is written, and `load` is idempotent, so a
failed run is never half an import you cannot reason about.

```bash
cd fetchers/whatsapp_import

# 1. What have we got? Changes nothing. Run this first.
uv run --group dev python -m whatsapp_import inspect

# 2. Backup -> a file. No database involved.
uv run --group dev python -m whatsapp_import extract \
  --self-jid 15551234567@s.whatsapp.net \
  --out ~/wa-history.ndjson

# 3. File -> database, through an SSH tunnel (see below).
uv run --group dev python -m whatsapp_import load --in ~/wa-history.ndjson
```

Then the normalizer turns the raw rows into people and interactions on its next
five-minute tick, or immediately with
`ssh memory 'sudo systemctl start memory-normalizer'`.

### The database connection

Postgres is bound to the droplet's loopback, so plain Tailscale to port 5432
does not reach it. Open a tunnel in another terminal:

```bash
ssh -N -L 15432:127.0.0.1:5432 memory
```

Then point the importer at it. Port 15432 is already the default:

```bash
export POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_DB=memory
```

Or set `WHATSAPP_IMPORT_DATABASE_URL` to a full connection string. You can also
copy the NDJSON to the droplet and run `load` there, where no tunnel is needed.

## What it imports, and what it deliberately does not

**Contacts, with real names.** This is the biggest win. The live bridge only
ever sees the name a contact chose for themselves, which for anyone who is not
a friend is often a shop name. The backup carries the name from your own
address book. Names only ever fill a gap; an existing name is never overwritten.

**Messages**, into the same `raw.whatsapp_message` table as live ingest, marked
`origin = 'iphone_backup'`. The normalizer cannot tell the difference.

**Groups, recorded but NOT switched on.** Importing history must never silently
start ingesting a group you did not opt into. The import only makes the list
you choose from complete, and gives it real titles. To bring in one group's
history later, enable it and re-run with `--only-chat`, which is far cheaper
than a full re-import.

**Skipped:** status posts, broadcast lists and channels; group system events
like "X joined", which are not messages and carry no id to deduplicate on; and
any message whose timestamp cannot be trusted.

**Not yet: media.** Text only for now. Voice notes in the archive are recorded
as voice messages but their audio stays in the backup, so **only live voice
notes get transcribed**, not historical ones.

## Running it twice is safe

Deduplication is on `(source_message_id, from_me)`, the same key the live
bridge uses, and the merge fills empty fields without overwriting full ones. A
second run reports `rows_actually_new: 0`.

That number is read back from the table rather than taken from the insert's own
row count, because an upsert cannot tell you how many rows were genuinely new
and a count that quietly included updates would make the check meaningless.

## If it fails on your WhatsApp version

The store is Core Data, and its column names drift between releases. Nothing
here assumes a column exists — missing ones come back as NULL — but a large
enough change will still stop it. Run `inspect` and send me the output; it
prints the tables, their columns, row counts and a message-type histogram, and
that is enough to adapt the parser.

## Tests

```bash
uv run --group dev pytest
```

40 tests, run against a synthetic ChatStorage database built to the real Core
Data shape rather than a mock, because the risk here is SQL and schema
assumptions and a mock would simply agree with whatever those assumptions are.

The ones that matter most: the 2001 epoch (reading a date against the Unix
epoch instead puts it 31 years out), rejecting timestamps that cannot be right
rather than writing a wrong one, the device-suffix rule matching the bridge
exactly so a person cannot import twice, and voice notes staying distinct from
shared audio so only the former reach transcription.


---

# Route 2: WhatsApp's own "Export Chat"

When a full backup will not fit on disk. WhatsApp can send you a single
conversation as plain text — a few megabytes for years of messages when you
choose **Without Media**.

## On the phone

Open the chat → tap the contact's name at the top → scroll down → **Export
Chat** → **Without Media** → send it to yourself (Mail, AirDrop, Files).

Repeat for each conversation worth keeping. WhatsApp has no bulk export, so
this part is unavoidably one chat at a time — start with the people who matter.

Drop all the files in one folder. The import side is a single command.

## On the Mac — many chats at once

An export says who it is *called* but never gives a number, so each file needs
one. `scan` does most of that for you: it reads the names off the filenames and
looks them up against people you already have from Telegram, LinkedIn and
Google Contacts, most of whom already have a phone number on file.

```bash
cd fetchers/whatsapp_import

# 1. Writes wa-map.csv, pre-filled wherever the name matched someone.
uv run --group dev python -m whatsapp_import scan ~/Downloads/wa-exports

# 2. Open wa-map.csv, fill in any blank phone_or_jid, delete rows you don't want.

# 3. Import everything in one go.
uv run --group dev python -m whatsapp_import chat-export-batch \
  --map wa-map.csv --self-name "the user"
```

`scan` never picks between two candidates. It marks the row AMBIGUOUS and lists
them, because attaching a conversation to the wrong person is much worse than
leaving a blank to fill in. A file that fails to parse is reported and the rest
of the batch still runs.

## One chat at a time

```bash
uv run --group dev python -m whatsapp_import chat-export \
  ~/Downloads/"WhatsApp Chat with Alice.txt" \
  --chat 3725551234 \
  --self-name "the user" \
  --name "Alice"
```

- `--chat` is **their** phone number, digits only. An export contains no phone
  numbers at all, so there is no way to work out whose chat it is — you have to
  say. For a group, pass the full `...@g.us` jid.
- `--self-name` must match your own name **exactly as it appears in the file**,
  because that is the only way to tell which messages you sent.
- `--name` is optional and records their display name.

Point it at a folder instead of a file to load several exports for the same
chat at once.

## What this route cannot do

- **No phone numbers, so no automatic identity.** In a group, senders are names
  with nothing behind them, so no person is recorded for them at all. A name is
  not an identity and pretending otherwise would create duplicate people.
- **No real names.** The backup carries your address book; an export does not.
- **No media, and no message ids.** Ids are synthesised from each message's own
  content, prefixed `exp:` so they can never be mistaken for real ones. That
  makes re-importing the same file a no-op, but the *same* message captured
  live carries a different id — so the loader refuses any export row at or
  after the moment live capture began for that chat. Export fills the past;
  live owns the present.

## Ambiguous dates

`15/03/2024` is unambiguous, `05/03/2024` is not. Rather than guess, the parser
picks the one date format that fits **every** line in the file and keeps the
export in chronological order. If none does, it stops and says so instead of
importing dates that are silently months wrong.
