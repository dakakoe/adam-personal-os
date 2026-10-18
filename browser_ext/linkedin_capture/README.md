# ADAM — LinkedIn capture (browser extension)

Adds the LinkedIn profile you're looking at to ADAM as a contact.

LinkedIn has no public API and blocks server-side fetching — Proxycurl died in
July 2026 and nothing replaced it. The profile page in your logged-in browser
is the only place that data exists, so the capture happens there and posts the
result to `merge_api`.

## What it reads

Same shape the CSV import produces, plus what the page shows and the export
doesn't:

| Field | Where it comes from |
|---|---|
| name, first/last | `h1`, JSON-LD `Person` |
| headline | top card |
| location | top card |
| current title / company | first "Present" Experience entry → top card → headline (`X at Y`) |
| company URL, avatar, connection degree | top card |
| about | `#about` section |
| experience, education | those sections, first 10 / 5 entries |
| emails, phones | typed in by hand — nothing reads them off LinkedIn |

Everything is editable in the popup before you save; a selector LinkedIn
renames costs you one blank field, not the capture.

## It never talks to LinkedIn

The capture is a pure read of the page already rendered in your tab. No fetch,
no click, no navigation — `host_permissions` covers only this install's own
API, so the extension *cannot* request anything from LinkedIn even in
principle.

There used to be a "Pull contact info" button that clicked LinkedIn's own
Contact-info link, navigated to `/in/<vanity>/overlay/contact-info/`, and came
back with `history.back()`. It was removed: two navigations per capture, and a
profile → overlay → back loop repeated across many profiles is a distinctive
automated pattern. Emails and phones are now fields you fill in yourself.

Worth being clear about what this does and doesn't buy you: reading a page you
opened is the smallest possible footprint, but LinkedIn's terms prohibit
automated extraction regardless, and their volume signals count the profiles
you *open*, which is your browsing rather than this tool. If you need many
contacts at once, use LinkedIn's own export (Settings → Data Privacy → Get a
copy of your data → Connections) and the CSV importer — that is the sanctioned
route and costs zero profile views.

## Where it lands

`POST /api/capture/linkedin` →

- `raw.linkedin_profile_capture` — one row per vanity, upserted
- `canonical.person` — matched by LinkedIn vanity, then by any scraped email,
  else created
- `canonical.identity` `source='linkedin'` — role/company/headline in `evidence`,
  the same keys the CSV importer writes, so the LinkedIn card, profile-builder
  bios and the authoritative-title prompt rule all pick it up
- `canonical.identity` `source='email'` for each email (an address already owned
  by someone else is reported back, never stolen — that's a merge decision)
- `memory.extracted_signal` for phones / websites / twitter
- `memory.profile.structured` — `current_company` / `current_role`, gaps only

Capturing the same profile twice refreshes it. It never creates a duplicate.

The live page **wins** over stored evidence, unlike the CSV enrichment where a
hand-typed role wins. You're looking at the profile as it is right now; the
stored value is the older claim.

## Install

Not in the Chrome Web Store — load it unpacked (it talks to your private API;
there's nothing to publish).

1. `chrome://extensions` → enable **Developer mode**
2. **Load unpacked** → select `browser_ext/linkedin_capture/`
3. Open the extension's **Settings** and paste the bearer token
   (`MERGE_API_BEARER_TOKEN` from the droplet's `.env`). Leave the API base at
   `https://merge.example.com`.
4. **Test connection** should say "Connected".

Works in any Chromium browser (Chrome, Edge, Brave, Arc). Firefox needs a
`browser_specific_settings` block and `background.scripts` instead of a service
worker — not done here.

## Why it works without a CORS change

Fetches from an MV3 service worker with a matching `host_permissions` entry
aren't subject to CORS, so there's no preflight and merge_api's CORS config is
untouched. Caddy's `@bearer` matcher routes any request carrying an
`Authorization` header straight to `:9100`, bypassing Authelia — which is why
the extension sends a token and never a cookie.

## Permissions, and why each one

| Permission | Reason |
|---|---|
| `activeTab` + `scripting` | inject the scraper into the tab you're on, only when you click the icon. No content script runs passively on LinkedIn. |
| `storage` | API base + token in `chrome.storage.local` (local to this browser profile, never synced to Google) |
| `host_permissions: merge.example.com` | the API call |
| `optional_host_permissions` | requested only if you point Settings at a different API host |

The token is read in `background.js` alone and never enters the LinkedIn page's
world.
