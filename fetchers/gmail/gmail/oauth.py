"""Interactive OAuth flow for Gmail. Run on a machine with a browser
(your Mac, not the droplet) since Google deprecated out-of-band flow in
2022 — the Installed App flow must round-trip through localhost.

After auth, the refresh token is printed; persist it into raw.gmail_account
on the droplet either with --emit-sql (prints an INSERT statement to paste)
or --emit-env (prints export commands for env-var-based deploy)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

log = logging.getLogger(__name__)


SCOPES_BY_NAME = {
    "gmail":          "https://www.googleapis.com/auth/gmail.readonly",
    "contacts":       "https://www.googleapis.com/auth/contacts.readonly",
    "other-contacts": "https://www.googleapis.com/auth/contacts.other.readonly",
    "calendar":       "https://www.googleapis.com/auth/calendar.readonly",
    # Write scope for the Telegram-capture → event feature. Includes read, so a
    # work account re-consented with this still serves the calendar sync.
    "calendar-write": "https://www.googleapis.com/auth/calendar.events",
    # Send scope for outreach email draft → send.
    "gmail-send": "https://www.googleapis.com/auth/gmail.send",
    # Modify scope for the mail client's two-way archive/star push (Phase 3c).
    # Includes read, so an account re-consented with this still serves the
    # gmail sync; lets merge_api add/remove INBOX/STARRED labels on threads.
    "gmail-modify": "https://www.googleapis.com/auth/gmail.modify",
}


def run_flow(credentials_path: str, scopes: list[str]) -> dict:
    """Open browser, get user consent, return the credentials dict
    including refresh_token. Raises if the user cancels."""
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
    # offline+consent so we ALWAYS get a refresh_token even on re-auth.
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )
    return {
        "refresh_token": creds.refresh_token,
        "token": creds.token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="gmail-oauth")
    p.add_argument("--credentials", required=True,
                   help="Path to credentials.json from Google Cloud Console (Desktop app type)")
    p.add_argument("--account", required=True,
                   help="The Gmail address you're about to authorize. Used only for the printed SQL/output.")
    p.add_argument("--scopes", default="gmail,contacts,other-contacts",
                   help="Comma-separated scope shortnames. Default authorizes Gmail read + "
                        "saved Contacts + 'Other contacts' (the auto-collected directory Gmail "
                        "builds up from people you've emailed). "
                        f"Available: {','.join(SCOPES_BY_NAME)}")
    p.add_argument("--emit", choices=["sql", "json", "env"], default="sql",
                   help="sql (default): print INSERT for raw.gmail_account; "
                        "json: print {email, refresh_token} JSON; "
                        "env: print export GMAIL_REFRESH_TOKEN_<EMAIL>=<token>")
    args = p.parse_args()

    creds_path = Path(args.credentials).expanduser()
    if not creds_path.exists():
        print(f"error: credentials file not found at {creds_path}", file=sys.stderr)
        return 2

    requested_scopes = [
        SCOPES_BY_NAME[s.strip()] for s in args.scopes.split(",") if s.strip()
    ]
    if not requested_scopes:
        print("error: --scopes resolved to empty list", file=sys.stderr)
        return 2

    out = run_flow(str(creds_path), scopes=requested_scopes)
    rt = out["refresh_token"]
    if not rt:
        print("error: Google did not return a refresh_token. "
              "Re-run; ensure access_type=offline + prompt=consent (already set in code) "
              "and revoke the app at https://myaccount.google.com/permissions first if needed.",
              file=sys.stderr)
        return 3

    email = args.account.strip().lower()

    if args.emit == "sql":
        # We pipe the SQL through stdin instead of -c "..." because SSH strips
        # one layer of quoting; the previous form let the remote bash parse
        # "(email,...)" as a subshell. printf with double quotes (no $/`/\)
        # is paste-safe and ssh forwards stdin to docker exec -i.
        sql = (
            "INSERT INTO raw.gmail_account (email, refresh_token, status) "
            f"VALUES ('{email}', '{rt}', 'active') "
            "ON CONFLICT (email) DO UPDATE SET "
            "refresh_token = EXCLUDED.refresh_token, status = 'active';"
        )
        print()
        print("=== copy + paste this one line ===")
        print(
            f'printf "%s" "{sql}" | ssh memory '
            f"'docker exec -i memory-postgres-1 psql -U memory -d memory'"
        )
    elif args.emit == "json":
        print(json.dumps({"email": email, "refresh_token": rt}, indent=2))
    else:
        # env var name based on email; sanitize
        var = "GMAIL_REFRESH_TOKEN_" + email.replace("@", "_AT_").replace(".", "_DOT_").upper()
        print(f"export {var}='{rt}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
