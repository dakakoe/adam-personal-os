#!/usr/bin/env bash
# Phase 2 — droplet hardening. Runs ON the droplet AS root.
#
# Invoked from laptop with a heredoc (preserves spaces in OPS_PUBKEY):
#
#   scp infra/bootstrap.sh root@$RESERVED_IP:/root/bootstrap.sh
#
#   ssh root@$RESERVED_IP bash -s <<EOF
#   export TS_AUTHKEY="$TS_AUTHKEY"
#   export OPS_PUBKEY="$(cat ~/.ssh/id_ed25519.pub)"
#   bash /root/bootstrap.sh
#   EOF
#
#   ssh root@$RESERVED_IP rm /root/bootstrap.sh
#
# DO NOT try to pass OPS_PUBKEY as `ssh host VAR="$(cat key.pub)" bash ...` —
# ssh concatenates args with spaces and the public key's internal spaces will
# break parsing on the remote.

set -euo pipefail

: "${TS_AUTHKEY:?TS_AUTHKEY must be set}"
: "${OPS_PUBKEY:?OPS_PUBKEY must be set, the ed25519 public key contents}"

echo "==> 1/8 System update"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y \
  ca-certificates curl gnupg lsb-release ufw fail2ban \
  unattended-upgrades apt-listchanges \
  htop ncdu tmux git jq acl

echo "==> 2/8 Create ops user"
if ! id ops >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" ops
  usermod -aG sudo ops
  echo "ops ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ops
  chmod 0440 /etc/sudoers.d/ops
  mkdir -p /home/ops/.ssh
  echo "$OPS_PUBKEY" > /home/ops/.ssh/authorized_keys
  chmod 700 /home/ops/.ssh
  chmod 600 /home/ops/.ssh/authorized_keys
  chown -R ops:ops /home/ops/.ssh
fi

echo "==> 3/8 Create code user (no sudo)"
if ! id code >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" code
  mkdir -p /home/code/.ssh
  echo "$OPS_PUBKEY" > /home/code/.ssh/authorized_keys
  chmod 700 /home/code/.ssh
  chmod 600 /home/code/.ssh/authorized_keys
  chown -R code:code /home/code/.ssh
fi

echo "==> 4/8 SSH hardening"
cat > /etc/ssh/sshd_config.d/99-memory.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
PermitEmptyPasswords no
X11Forwarding no
AllowUsers ops code
EOF
systemctl restart ssh

echo "==> 5/8 Unattended upgrades"
echo 'APT::Periodic::Update-Package-Lists "1";' > /etc/apt/apt.conf.d/20auto-upgrades
echo 'APT::Periodic::Unattended-Upgrade "1";' >> /etc/apt/apt.conf.d/20auto-upgrades
sed -i 's|//\s*"\${distro_id}:\${distro_codename}-updates";|"${distro_id}:${distro_codename}-updates";|' /etc/apt/apt.conf.d/50unattended-upgrades

echo "==> 6/8 fail2ban"
cat > /etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban

echo "==> 7/8 UFW"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'ssh, will close after tailscale'
ufw allow 80/tcp comment 'caddy http'
ufw allow 443/tcp comment 'caddy https'
ufw allow in on tailscale0
ufw --force enable

echo "==> 8/8 Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="$TS_AUTHKEY" --hostname=memory --ssh

echo
echo "======================================================================"
echo "Bootstrap done."
echo "Tailscale IPv4: $(tailscale ip -4 || true)"
echo
echo "From your laptop:"
echo "  1. Test: ssh ops@memory  (uses Tailscale MagicDNS)"
echo "  2. If it works, close public SSH:"
echo "       ssh memory 'sudo ufw delete allow 22/tcp'"
echo "  3. Then update DO Cloud Firewall to remove port 22."
echo "======================================================================"
