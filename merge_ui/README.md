# memory · merge UI

Self-hosted UI for the personal memory system. Lives on the droplet at `merge.example.com`, talks to the FastAPI `merge_api` on the same host.

## Quick start (local)

```bash
cd merge_ui
pnpm install                              # or npm install
npx shadcn@latest init -d                 # accepts existing components.json
npx shadcn@latest add \
  button card dialog alert-dialog input label badge avatar \
  separator select dropdown-menu command alert tooltip
pnpm dev                                  # next on http://127.0.0.1:3100
```

In a separate shell, run the API:

```bash
cd ../merge_api
uv venv -p 3.12
uv pip install --python .venv/bin/python -e .
MERGE_API_BEARER_TOKEN=<your-token> \
POSTGRES_USER=memory POSTGRES_PASSWORD=… POSTGRES_DB=memory \
.venv/bin/python -m merge_api
```

Open http://127.0.0.1:3100, paste the bearer at /login. The UI sets an httpOnly cookie and you're in.

## What's here

| Path | What it is |
|---|---|
| `app/persons/` | List + detail of canonical persons. Detail has avatar + identity chips + add/remove + summary + recent messages |
| `app/merge/` | Queue of merge candidates. Side-by-side cards, keyboard-driven (y/n/s/t) |
| `app/login/` | Bearer-token login → cookie |
| `components/person-card.tsx` | The big person card you see at `/persons/[id]` |
| `components/identity-chip.tsx` | Channel-coloured chip with hover-to-remove |
| `components/add-identity-dialog.tsx` | Add a new identity (`Dialog`) |
| `components/remove-identity-confirm.tsx` | Destructive confirm (`AlertDialog`) |
| `components/merge-queue.tsx` | Side-by-side merge review |
| `components/command-palette.tsx` | ⌘K global person search |
| `lib/api.ts` | typed fetch wrapper; same-origin in browser, server-side via `MERGE_API_URL` |

## Keyboard

| Where | Key | Action |
|---|---|---|
| Anywhere | ⌘K | Search people |
| Merge queue | y | Approve merge (opens confirm) |
| Merge queue | n | Reject |
| Merge queue | s | Defer |
| Merge queue | t | Swap winner |

## Theme

Dark-mode-first, zinc base, indigo accent (`oklch(0.62 0.18 280)` — Linear-ish), Geist Sans + Geist Mono. All in `app/globals.css`.
