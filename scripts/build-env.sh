#!/usr/bin/env bash
# Build a real .env from .env.example with strong random secrets.
# Idempotent-safe: refuses to overwrite an existing .env unless --force is passed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ -f .env && "$FORCE" -ne 1 ]]; then
  echo ".env already exists. Refusing to overwrite."
  echo "If you really want to regenerate, run:  bash scripts/build-env.sh --force"
  exit 1
fi

if [[ ! -f .env.example ]]; then
  echo "Missing .env.example. Are you in the repo root?"
  exit 1
fi

PG_PASS=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)
APP_SECRET=$(openssl rand -base64 48 | tr -d '=+/' | cut -c1-48)

cp .env.example .env

# Detect sed flavor (BSD on macOS vs GNU on Linux)
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(-i)
else
  SED_INPLACE=(-i '')
fi

sed "${SED_INPLACE[@]}" "1,/CHANGEME_run_openssl_rand_base64_32/ s|CHANGEME_run_openssl_rand_base64_32|${PG_PASS}|" .env
sed "${SED_INPLACE[@]}" "s|CHANGEME_run_openssl_rand_base64_32|${APP_SECRET}|" .env
sed "${SED_INPLACE[@]}" "s|CHANGEME_same_as_POSTGRES_PASSWORD|${PG_PASS}|" .env

if grep -q CHANGEME .env; then
  echo "WARNING: some CHANGEME placeholders remain:"
  grep -n CHANGEME .env
  exit 1
fi

chmod 600 .env

echo "OK — .env written with random secrets."
echo "Permissions: $(stat -f '%Sp' .env 2>/dev/null || stat -c '%A' .env)"
echo
echo "Git should NOT see this file:"
git status --short .env || true
