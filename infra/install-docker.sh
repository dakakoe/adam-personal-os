#!/usr/bin/env bash
# Install Docker CE on Ubuntu 24.04 (and add `ops` to the docker group).
# Run on the droplet as `ops` with sudo, or pipe through ssh.

set -euo pipefail

echo "==> 1/5 apt prerequisites"
sudo install -m 0755 -d /etc/apt/keyrings
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release

echo "==> 2/5 Docker GPG key"
if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
fi

echo "==> 3/5 Docker apt repo"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "==> 4/5 Install Docker CE + Compose plugin"
sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "==> 5/5 Add ops to docker group"
sudo usermod -aG docker ops

echo
echo "Docker version: $(sudo docker --version)"
echo "Compose plugin: $(sudo docker compose version)"
echo
echo "IMPORTANT: log out and back in (or run 'newgrp docker') so the group change takes effect."
