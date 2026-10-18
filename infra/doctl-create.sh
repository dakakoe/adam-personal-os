#!/usr/bin/env bash
# Phase 1 — create the droplet, reserved IP, and Cloud Firewall.
# Run this from your laptop. Requires:
#   - doctl authenticated (`doctl auth init`)
#   - SSH key already uploaded to DO
# This script is idempotent-safe to read; it will NOT re-create if a droplet named
# "memory" already exists. Edit and re-run individual blocks if you need to retry.

set -euo pipefail

# ---- Load secrets ----
if [[ -f "$HOME/.config/memory/secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.config/memory/secrets.env"
fi

: "${DO_TOKEN:?DO_TOKEN must be set in ~/.config/memory/secrets.env}"

# Re-auth doctl in case the shell is fresh
doctl auth init -t "$DO_TOKEN" >/dev/null

# ---- Config ----
DROPLET_NAME="memory"
REGION="sgp1"
SIZE="s-4vcpu-8gb-intel"
IMAGE="ubuntu-24-04-x64"
FIREWALL_NAME="memory-fw"

# ---- Check for existing droplet ----
if doctl compute droplet list --no-header --format Name | grep -qx "$DROPLET_NAME"; then
  echo "Droplet '$DROPLET_NAME' already exists. Refusing to re-create."
  echo "If you intended to start fresh, destroy it first:"
  echo "  doctl compute droplet delete $DROPLET_NAME"
  exit 1
fi

# ---- Get SSH key ID ----
SSH_KEY_ID=$(doctl compute ssh-key list --no-header --format ID | head -1)
if [[ -z "$SSH_KEY_ID" ]]; then
  echo "No SSH key found on DO. Upload one first:"
  echo "  doctl compute ssh-key import id_ed25519 --public-key-file ~/.ssh/id_ed25519.pub"
  exit 1
fi
echo "Using SSH key ID: $SSH_KEY_ID"

# ---- Create droplet ----
echo "Creating droplet '$DROPLET_NAME' ($SIZE in $REGION)..."
doctl compute droplet create "$DROPLET_NAME" \
  --image "$IMAGE" \
  --size "$SIZE" \
  --region "$REGION" \
  --ssh-keys "$SSH_KEY_ID" \
  --enable-backups \
  --enable-monitoring \
  --enable-ipv6 \
  --wait

DROPLET_ID=$(doctl compute droplet list "$DROPLET_NAME" --no-header --format ID | head -1)
echo "Droplet ID: $DROPLET_ID"

# ---- Reserved IP ----
echo "Creating reserved IP in $REGION..."
doctl compute reserved-ip create --region "$REGION"
# Pick the most recently created reserved IP that is not yet assigned
RESERVED_IP=$(doctl compute reserved-ip list --no-header --format IP,DropletID \
  | awk '$2 == "<nil>" || $2 == "" {print $1; exit}')

if [[ -z "$RESERVED_IP" ]]; then
  echo "Could not determine the new reserved IP. Inspect with: doctl compute reserved-ip list"
  exit 1
fi
echo "Reserved IP: $RESERVED_IP"

doctl compute reserved-ip-action assign "$RESERVED_IP" "$DROPLET_ID"

# ---- Cloud Firewall ----
echo "Creating firewall '$FIREWALL_NAME'..."
doctl compute firewall create \
  --name "$FIREWALL_NAME" \
  --droplet-ids "$DROPLET_ID" \
  --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
  --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"

echo
echo "======================================================================"
echo "Done. Now:"
echo "  1. Add A records in GoDaddy:"
echo "       memory.example.com  ->  $RESERVED_IP"
echo "       crm.example.com     ->  $RESERVED_IP"
echo "  2. Verify DNS:"
echo "       dig +short memory.example.com"
echo "       dig +short crm.example.com"
echo "  3. Update ~/.ssh/config Host memory HostName to $RESERVED_IP"
echo "  4. Test SSH:  ssh root@$RESERVED_IP"
echo "  5. Then run Phase 2 bootstrap."
echo "======================================================================"
