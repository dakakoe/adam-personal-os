# WhatsApp ingest bridge

Links the droplet to WhatsApp as a **companion device** (the same mechanism as
WhatsApp Web and Desktop) and writes what it sees into `raw.whatsapp_*`.
Everything downstream — persons, interactions, follow-ups, search — is handled
by the Python normalizer, exactly as it is for Telegram.

## Read-only, by construction

There is no `sendMessage` call in this package. Not disabled, not gated by a
flag: absent. That absence is the boundary of the feature, and it is deliberate.

## The risk, stated plainly

Baileys is an unofficial client and using it is against WhatsApp's terms.
Accounts do get banned. In practice bans overwhelmingly hit accounts that send
automated or bulk messages, which is precisely what this does not do, but the
risk is low rather than zero. Three design choices exist to keep it low:

- `markOnlineOnConnect: false` — we never announce presence, so the account
  does not look like it is online 24 hours a day, and the user's phone keeps
  delivering notifications normally.
- Group metadata is cached for six hours. Fetching a group's subject on every
  message would be both slow and a recognisable traffic pattern.
- Only one instance may ever run against a session. If another client claims
  it, this one exits 3 and systemd is configured not to restart it, rather than
  starting a tug-of-war that loses messages for both.

## Layout

| File | Role |
|---|---|
| `src/index.js` | umask, logging, subcommand dispatch |
| `src/config.js` | env to config, mirroring the Python workers' convention |
| `src/db.js` | every SQL statement; writes `raw.*` and the health row only |
| `src/jid.js` | pure: JID normalization, chat keys, phone extraction |
| `src/message.js` | pure: message kind, text, mentions, timestamps |
| `src/live.js` | the socket, event handlers, reconnect policy |
| `src/media.js` | voice-note and media download |
| `src/pair.js` | pairing-code auth and its marker-line protocol |

`jid.js` and `message.js` hold the logic the whole ingest's correctness rests
on, which is why they are pure and why they are the ones with tests.

## Install and deploy

`node_modules` is never rsynced; the droplet builds in place, the same way
`merge_ui` does.

```bash
rsync -av --exclude node_modules --exclude 'session*' \
  fetchers/whatsapp/ memory:/srv/memory/apps/whatsapp/
ssh memory 'cd /srv/memory/apps/whatsapp && npm ci --omit=dev --omit=optional'
```

`npm ci` rather than `npm install`, so a partial rsync can never leave a
half-built tree. `--omit=optional` skips Baileys' four optional peers
(`sharp`, `jimp`, `link-preview-js`, `qrcode-terminal`); all four exist only for
sending rich messages or drawing a QR code, and `sharp` would pull a native
build onto the droplet for nothing.

Unit file: `infra/systemd/memory-whatsapp.service`.

## Pairing

The droplet is headless, so linking uses a **pairing code** rather than a QR
image: an eight-character code survives a web UI, an SSH session and a log
file, which a bitmap does not.

The session is a single-writer store, so the listener must be stopped first —
the same constraint the Telethon session has.

Set `WHATSAPP_PHONE` in `/srv/memory/secrets/.env` first — full international
number, digits only, no `+`. The file is owned by `ops`, which is the SSH user,
so no sudo is needed:

```bash
ssh memory 'echo "WHATSAPP_PHONE=372XXXXXXXX" >> /srv/memory/secrets/.env'
```

Then pair. Note the explicit `set -a && . …/.env`: systemd supplies the
environment through `EnvironmentFile=`, so a hand-run process gets nothing
unless you source it yourself, and the first thing it will complain about is a
missing `POSTGRES_USER`.

```bash
ssh -t memory 'cd /srv/memory/apps/whatsapp \
  && sudo systemctl stop memory-whatsapp \
  && set -a && . /srv/memory/secrets/.env && set +a \
  && node src/index.js pair'
```

It prints `pairing_code code=ABCD1234`. On the phone: **WhatsApp → Settings →
Linked Devices → Link a Device → Link with phone number instead**, then enter
the code. When it reports `paired`, start the unit again.

```bash
sudo systemctl start memory-whatsapp
```

`WHATSAPP_PHONE` must be set to the full international number, digits only.

If a session already exists, `pair` refuses with `session_exists`. Clear it
first with `node src/index.js reset`, which **archives** the session directory
rather than deleting it.

### Marker lines

`pair` prints stable markers on stdout for `merge_api/setup_flow.py` to parse,
and mirrors its progress into `memory.source_status` so the setup wizard can
poll instead of holding a pipe open for three minutes.

| Marker | Exit | Meaning |
|---|---|---|
| `already_authorized` | 0 | a usable session already exists |
| `pairing_code code=…` | — | printed immediately; the process keeps running |
| `paired jid=…` | 0 | the phone accepted the code |
| `pair_timeout` | 4 | nobody entered it in time |
| `bad_phone` | 2 | `WHATSAPP_PHONE` missing or not a number |
| `session_exists` | 2 | run `reset` first |

## Environment

| Variable | Default | Notes |
|---|---|---|
| `WHATSAPP_PHONE` | — | full international number, digits only; needed only to pair |
| `WHATSAPP_DATABASE_URL` | built from `POSTGRES_*` | same convention as every Python worker |
| `WHATSAPP_SESSION_DIR` | `/srv/memory/apps/whatsapp/session` | mode 700; never committed or rsynced |
| `WHATSAPP_VOICE_DIR` | `/srv/memory/data/whatsapp-voice` | what Whisper transcribes |
| `WHATSAPP_MEDIA_DIR` | `/srv/memory/data/whatsapp-media` | only used when media download is on |
| `WHATSAPP_DOWNLOAD_VOICE` | `true` | voice notes become message bodies via Whisper |
| `WHATSAPP_DOWNLOAD_MEDIA` | `false` | images and video cost disk and nothing can read them |
| `WHATSAPP_PAIR_TIMEOUT_SEC` | `180` | how long `pair` waits for the code to be entered |
| `WHATSAPP_LOG_LEVEL` | `info` | `debug` also un-silences Baileys, which logs message contents |

## The session is a credential

Anyone holding `session/` can read every message the account receives. It is
mode 600 inside a 700 directory, gitignored, and excluded from rsync — the same
treatment as `telethon/session/memory.session`.

## Groups are opt-in

A group is recorded on first sighting with `enabled = false` and **none of its
messages are ingested** until it is enabled, matching
`raw.telegram_group_allowlist`. Until the groups UI ships, enable one directly:

```sql
UPDATE raw.whatsapp_group_allowlist
   SET enabled = true, enabled_at = now()
 WHERE chat_jid = '120363...@g.us';
```

Enabling a group ingests every member's messages and creates a person for each
of them. That is the same behaviour Telegram groups have, but it is worth
knowing before you enable a busy one.

## What this does not do

- **No history.** A newly linked device receives almost none. The archive comes
  from the iPhone-backup importer (`fetchers/whatsapp_import`). Pair *first*,
  then take the backup, or you lose the window in between.
- **No sending.** See above.
- **No status, broadcast or channel content.** Dropped at the door.
- Reactions and protocol messages are dropped too; they carry nothing worth
  remembering and would inflate interaction counts.

## Tests

```bash
npm test        # node --test, no framework
npm run typecheck
```

Covers JID normalization (including the device-suffix case, which is the one
that silently splits a contact in two), the LID and phone namespaces staying
distinct, message kind and text extraction through wrapper layers, mention
deduplication, and timestamp rejection.
