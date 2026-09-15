#!/usr/bin/env bash
# Publish the current private HEAD as a fresh, scrubbed, single-commit snapshot
# to the PUBLIC repo. The public repo is a point-in-time mirror, not a live one:
# each run force-pushes ONE commit (no history is ever published).
#
#   PUBLIC_REMOTE=git@github.com:dakakoe/adam-personal-os.git ./scripts/publish-public.sh
#   DRY_RUN=1 ./scripts/publish-public.sh      # scrub + scan only; no push, keeps the staged tree
#
# Safety: the scrub (scripts/publish/scrub.py) runs a leak scan and exits
# non-zero if any known personal pattern remains; `set -e` aborts before any
# push. Run this from YOUR terminal — an agent's push to a new remote is
# (correctly) blocked by Claude Code's exfiltration guard.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"

if [[ "$DRY_RUN" != "1" ]]; then
  : "${PUBLIC_REMOTE:?set PUBLIC_REMOTE to the public repo git URL, e.g. git@github.com:you/adam-personal-os.git}"
fi

STAGE="$(mktemp -d)"
trap '[[ "$DRY_RUN" == "1" ]] || rm -rf "$STAGE"' EXIT

echo "==> archiving private HEAD ($(git -C "$REPO_ROOT" rev-parse --short HEAD))"
git -C "$REPO_ROOT" archive HEAD | tar -x -C "$STAGE"

echo "==> scrubbing"
python3 "$REPO_ROOT/scripts/publish/scrub.py" "$STAGE" "$REPO_ROOT/scripts/publish/templates"
# ^ exits non-zero (aborting here) if the leak scan finds anything.

if [[ "$DRY_RUN" == "1" ]]; then
  echo "==> DRY_RUN: staged clean tree at $STAGE (not pushed). Inspect it, then remove it."
  exit 0
fi

# --- version + release notes -------------------------------------------------
# The mirror is force-pushed as ONE commit, so git history can't tell an
# installed copy whether it's current. A VERSION file (baked into the tree) plus
# a GitHub Release give every install something to compare against — that's what
# powers the in-app "update available" banner, and it lets anyone subscribe with
# GitHub's own Watch → Releases.
#
# CalVer YYYY.MM.DD, with .N when publishing more than once in a day.
VERSION="${VERSION:-$(date +%Y.%m.%d)}"
if command -v gh >/dev/null 2>&1 && [[ -n "${PUBLIC_REPO:-}" ]]; then
  n=1
  while gh release view "v$VERSION" --repo "$PUBLIC_REPO" >/dev/null 2>&1; do
    n=$((n + 1)); VERSION="$(date +%Y.%m.%d).$n"
  done
fi
printf '%s\n' "$VERSION" > "$STAGE/VERSION"
echo "==> version $VERSION"

# Release notes are YOURS to write. They are NOT auto-generated from private
# commit subjects on purpose: the leak scan checks FILES, not commit messages,
# and yours legitimately name real people and accounts. Never pipe them out.
NOTES_FILE="${NOTES_FILE:-$(mktemp)}"
if [[ ! -s "$NOTES_FILE" ]]; then
  cat > "$NOTES_FILE" <<'TPL'
# Release notes for this ADAM snapshot — plain text, shown to anyone who
# installed it (in-app banner + GitHub Releases). Lines starting with # are
# dropped. Write for someone who is NOT you: no private names/accounts.
#
# What's new
# -
TPL
  "${EDITOR:-vi}" "$NOTES_FILE"
fi
NOTES="$(grep -v '^[[:space:]]*#' "$NOTES_FILE" | sed '/^[[:space:]]*$/d' || true)"
[[ -z "$NOTES" ]] && NOTES="Maintenance snapshot."

echo "==> building fresh single commit + force-pushing to $PUBLIC_REMOTE"
(
  cd "$STAGE"
  git init -q -b main
  git add -A
  git -c user.name="ADAM" -c user.email="noreply@users.noreply.github.com" \
      commit -q -m "ADAM · Personal OS — public snapshot $VERSION"
  git push --force "$PUBLIC_REMOTE" main
)

# Tagged Release = the thing installs poll, and what Watch→Releases emails on.
if command -v gh >/dev/null 2>&1 && [[ -n "${PUBLIC_REPO:-}" ]]; then
  gh release create "v$VERSION" --repo "$PUBLIC_REPO" \
     --title "ADAM $VERSION" --notes "$NOTES" --target main \
    && echo "==> release v$VERSION published"
else
  echo "==> NOTE: set PUBLIC_REPO=owner/name (and install gh) to cut a Release;"
  echo "    without it installs can't see that an update exists."
fi
echo "==> published. The public repo now holds one clean commit of your latest HEAD."
