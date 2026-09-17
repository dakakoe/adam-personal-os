# Web-based Google re-consent (Sources page "Reconnect") — deploy

Lets you re-authorize a revoked Gmail/GCal account from the Sources page instead
of the `gmail-oauth` CLI. **Inert until configured** — the endpoints 503 and the
UI hides the button until `MERGE_OAUTH_REDIRECT_URI` is set, so deploying the
code changes nothing.

## The catch: Desktop → Web OAuth client

The current `GMAIL_CLIENT_SECRETS` is a **Desktop-app** OAuth client (loopback
redirect only). A server-side `https://…/callback` requires a **Web-application**
client. Refresh tokens are bound to the client that minted them, so the fetchers
and this flow must share one client.

**Consequence:** when you switch `GMAIL_CLIENT_SECRETS` to the Web client, the
existing Desktop-minted refresh tokens stop refreshing → every Google account
flips to `reauth_needed` once → you reconnect each via the new button. That's
self-healing (it's what this feature is for), but it is a one-time forced
re-consent of all accounts. Do it when you can spend 5 minutes clicking through.

## 1. GCP — create the Web OAuth client
APIs & Services → Credentials → Create credentials → OAuth client ID →
**Web application**.
- Authorized redirect URI: `https://merge.example.com/api/sources/oauth/callback`
- Download the JSON (it has a top-level `"web"` key).

Make sure the OAuth **consent screen** lists the scopes you request
(`gmail.readonly`, `contacts.readonly`, `contacts.other.readonly`,
`calendar.readonly`) and the publishing status is **In production** — a client
left in **Testing** issues refresh tokens that expire after 7 days, which would
make accounts demand reconnection weekly.

## 2. Droplet — install secrets + env
```bash
# Replace the client secrets the fetchers + merge_api read (BACK UP FIRST):
ssh memory 'cp /srv/memory/secrets/gmail-client-secrets.json \
  /srv/memory/secrets/gmail-client-secrets.desktop.bak'
rsync -av web-client.json memory:/srv/memory/secrets/gmail-client-secrets.json
```
Add to `/srv/memory/secrets/.env`:
```
MERGE_OAUTH_REDIRECT_URI=https://merge.example.com/api/sources/oauth/callback
# optional — base scopes every account gets. Default: gmail,contacts,other-contacts,calendar
# MERGE_OAUTH_SCOPES=gmail,contacts,other-contacts,calendar
```
> Per-account scopes are automatic: the account matching `BOT_WORK_CALENDAR_
> ACCOUNT` additionally gets `calendar.events`, and the one matching
> `BOT_EMAIL_SEND_ACCOUNT` gets `gmail.send` — so reconnecting the work account
> never strips its write scopes, and the personal account isn't over-permissioned.
> (Set those env vars for the tailoring to apply.)

## 3. Restart
```bash
ssh memory 'cd /srv/memory && docker compose --env-file secrets/.env up -d merge-api \
  && systemctl restart memory-gmail.service memory-gcal.service || true'
```

## 4. Reconnect each account
- Visit `https://merge.example.com/sources`. Gmail + GCal show **"reconnect needed"**.
- Click **Reconnect with Google** → consent → it returns to `/sources?reconnected=<email>`.
- The card clears; syncing resumes on the next worker run.

## Verify
- `GET /api/sources` returns `reconnect_url` non-null for gmail/gcal.
- A completed reconnect writes `refresh_token` + `status='active'` to
  `raw.gmail_account` (check: `SELECT email,status FROM raw.gmail_account;`).
- Tampering with the `state` param → redirected back with `?reconnect_error=bad_state`.

## Troubleshooting
- **Bounced to the Authelia login on the way back from Google** → the Authelia
  session cookie isn't being sent on the cross-site callback. It must be
  `SameSite=Lax` (Authelia's default — don't set it to `strict`).
- **Accounts immediately demand reconnect again** → the OAuth consent screen is
  in **Testing** mode (7-day refresh-token expiry). Publish it.

## Rollback
Restore the Desktop secrets backup and unset `MERGE_OAUTH_REDIRECT_URI`; the UI
falls back to the CLI disclosure. (Accounts reconnected against the Web client
will then need a one-time CLI re-mint against the Desktop client.)
