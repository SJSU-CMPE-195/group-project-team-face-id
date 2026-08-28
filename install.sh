#!/usr/bin/env bash
# Install the single Pi Device API service.
# Usage: bash install.sh
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${FACEID_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
if [[ -z "$SERVICE_HOME" || "$SERVICE_USER" == "root" ]]; then
  echo "Set FACEID_SERVICE_USER to a non-root local account." >&2
  exit 1
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
DB_DIR="${FACEID_DB_DIR:-$SERVICE_HOME/faceid}"
VENV_DIR="$PROJECT_DIR/.venv"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_NAME="faceid-api.service"

if [[ "$PROJECT_DIR" == /root || "$PROJECT_DIR" == /root/* ]]; then
  echo "Move the checkout outside /root so $SERVICE_USER can read it." >&2
  exit 1
fi
if [[ "$PROJECT_DIR" == *$'\n'* || "$PROJECT_DIR" == *$'\r'* || "$PROJECT_DIR" == *'"'* || "$PROJECT_DIR" == *'%'* ||
      "$DB_DIR" == *$'\n'* || "$DB_DIR" == *$'\r'* || "$DB_DIR" == *'"'* || "$DB_DIR" == *'%'* ]]; then
  echo "Project and database paths cannot contain quotes, percent signs, or newlines." >&2
  exit 1
fi

echo "=== FaceID install ==="
echo "Project: $PROJECT_DIR"
echo "Service user: $SERVICE_USER"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo ""
echo "[1/6] Installing Raspberry Pi OS packages..."
$SUDO apt-get update
$SUDO apt-get install -y curl python3-dev python3-venv python3-picamera2

echo ""
echo "[2/6] Creating the project virtual environment..."
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  # Picamera2 is an apt-managed system package; expose it to this venv.
  python3 -m venv --system-site-packages "$VENV_DIR"
elif ! grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV_DIR/pyvenv.cfg"; then
  echo "Existing $VENV_DIR does not expose Raspberry Pi OS packages." >&2
  echo "Move it aside, then rerun install.sh so Picamera2 is available." >&2
  exit 1
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

echo ""
echo "[3/6] Installing Python runtime dependencies..."
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements-pi-device-api.txt"

echo ""
echo "[4/6] Initializing the SQLite database and device permissions..."
$SUDO install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$DB_DIR"
if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
  FACEID_DB_PATH="$DB_DIR/faceid.db" "$VENV_DIR/bin/python" "$PROJECT_DIR/db.py"
elif [[ "$(id -u)" -eq 0 ]]; then
  runuser -u "$SERVICE_USER" -- env FACEID_DB_PATH="$DB_DIR/faceid.db" "$VENV_DIR/bin/python" "$PROJECT_DIR/db.py"
else
  sudo -u "$SERVICE_USER" -- env FACEID_DB_PATH="$DB_DIR/faceid.db" "$VENV_DIR/bin/python" "$PROJECT_DIR/db.py"
fi
$SUDO chown -R "$SERVICE_USER":"$SERVICE_GROUP" "$DB_DIR"
DEVICE_GROUPS="$(for group in dialout video gpio; do if getent group "$group" >/dev/null; then printf '%s,' "$group"; fi; done)"
DEVICE_GROUPS="${DEVICE_GROUPS%,}"
if [[ -n "$DEVICE_GROUPS" ]]; then
  $SUDO usermod -aG "$DEVICE_GROUPS" "$SERVICE_USER"
fi

echo ""
echo "[5/6] Installing the API systemd unit..."
# The checked-in unit keeps a readable Pi default path; replace it for the
# actual checkout so the same installer works from any clone directory.
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}
PROJECT_REPLACEMENT="$(escape_sed_replacement "$PROJECT_DIR")"
DB_FILE_REPLACEMENT="$(escape_sed_replacement "$DB_DIR/faceid.db")"
USER_REPLACEMENT="$(escape_sed_replacement "$SERVICE_USER")"
sed \
  -e "s|User=pi|User=$USER_REPLACEMENT|" \
  -e "s|/home/pi/faceid/group-project-team-face-id-main|$PROJECT_REPLACEMENT|g" \
  -e "s|FACEID_DB_PATH=/home/pi/faceid/faceid.db|FACEID_DB_PATH=$DB_FILE_REPLACEMENT|" \
  "$PROJECT_DIR/systemd/faceid-api.service" \
  | $SUDO tee "$SYSTEMD_DIR/$SERVICE_NAME" >/dev/null

# Older installs used a second camera/serial process. It must not compete for
# the camera or ESP32 now that the root Device API owns the full hardware path.
$SUDO systemctl disable --now faceid-verify.service 2>/dev/null || true
$SUDO rm -f "$SYSTEMD_DIR/faceid-verify.service"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME"

echo ""
echo "[6/6] Starting the API and running a health smoke check..."
$SUDO systemctl restart "$SERVICE_NAME"
SERVICE_PORT=5000
if [[ -r /etc/default/faceid ]]; then
  CONFIGURED_PORT="$(sed -nE 's/^[[:space:]]*PORT=([0-9]+)[[:space:]]*$/\1/p' /etc/default/faceid | tail -n 1)"
  if [[ -n "$CONFIGURED_PORT" && "$CONFIGURED_PORT" -ge 1 && "$CONFIGURED_PORT" -le 65535 ]]; then
    SERVICE_PORT="$CONFIGURED_PORT"
  fi
fi
for attempt in {1..20}; do
  if curl --fail --silent --show-error "http://127.0.0.1:$SERVICE_PORT/health" >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 20 ]]; then
    echo "Health check failed; recent service logs:" >&2
    $SUDO journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
    exit 1
  fi
  sleep 1
done

echo ""
echo "=== Done ==="
echo "  faceid-api: $($SUDO systemctl is-active "$SERVICE_NAME")"
echo "  health:     http://127.0.0.1:$SERVICE_PORT/health"
echo "  Pi IP:      $(hostname -I)"
echo ""
echo "Useful commands:"
echo "  sudo journalctl -u faceid-api -f"
echo "  sudo systemctl restart faceid-api"
