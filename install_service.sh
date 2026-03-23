#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-cpu-monitor}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$USER}}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (for example: sudo ./install_service.sh)." >&2
  exit 1
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 was not found in PATH." >&2
  exit 1
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "User '${SERVICE_USER}' does not exist." >&2
  exit 1
fi

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Raspberry Pi CPU Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/cpu_monitor.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

echo "Installed and started ${SERVICE_NAME}.service"
echo "Check status with: systemctl status ${SERVICE_NAME}.service"
