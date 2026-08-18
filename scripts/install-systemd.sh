#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_file="${project_dir}/deploy/systemd/wecom-hermes-agent-bridge.service"

if [[ ! -f /etc/wecom-hermes-agent-bridge/bridge.env ]]; then
  echo "Create /etc/wecom-hermes-agent-bridge/bridge.env first." >&2
  exit 1
fi

if ! id wecom-bridge >/dev/null 2>&1; then
  useradd --system --home /var/lib/wecom-hermes-agent-bridge --shell /usr/sbin/nologin wecom-bridge
fi

chown root:wecom-bridge /etc/wecom-hermes-agent-bridge/bridge.env
chmod 0640 /etc/wecom-hermes-agent-bridge/bridge.env
install -d -o wecom-bridge -g wecom-bridge -m 0700 /var/lib/wecom-hermes-agent-bridge
install -m 0644 "$service_file" /etc/systemd/system/wecom-hermes-agent-bridge.service
systemctl daemon-reload
systemctl enable --now wecom-hermes-agent-bridge.service
