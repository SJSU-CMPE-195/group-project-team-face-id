#!/usr/bin/env bash
# Install the wireless service without replacing or stopping a legacy FaceID service.
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${FACEID_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_HOME="$({ getent passwd "$SERVICE_USER" || true; } | cut -d: -f6)"
if [[ -z "$SERVICE_HOME" || "$SERVICE_USER" == "root" ]]; then
  echo "Set FACEID_SERVICE_USER to a non-root local account." >&2
  exit 1
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
DB_DIR="${FACEID_DB_DIR:-$SERVICE_HOME/faceid}"
DB_PATH="${FACEID_DB_PATH:-$DB_DIR/faceid.db}"
CONFIG_PATH="${BASS_DEVICE_CONFIG:-$DB_DIR/device.json}"
CONFIG_SOURCE="${BASS_DEVICE_CONFIG_SOURCE:-}"
SERVICE_PORT="${BASS_PORT:-5056}"
VENV_DIR="$PROJECT_DIR/.venv-wireless"
UNIT_SOURCE="$PROJECT_DIR/systemd/faceid-wireless.service"
UNIT_DESTINATION="/etc/systemd/system/faceid-wireless.service"
DASHBOARD_INDEX="$PROJECT_DIR/dist/index.html"

if [[ ! -f "$DASHBOARD_INDEX" ]]; then
  echo "Missing built dashboard: $DASHBOARD_INDEX" >&2
  echo "Build it on a development machine or CI, then copy dist/ into $PROJECT_DIR before installing." >&2
  exit 1
fi

if [[ ! "$SERVICE_PORT" =~ ^[0-9]+$ ]] || (( SERVICE_PORT < 1 || SERVICE_PORT > 65535 )); then
  echo "BASS_PORT must be between 1 and 65535." >&2
  exit 1
fi
if [[ "$PROJECT_DIR" == /root || "$PROJECT_DIR" == /root/* ]]; then
  echo "Move the checkout outside /root so $SERVICE_USER can read it." >&2
  exit 1
fi
for path_value in "$PROJECT_DIR" "$DB_PATH" "$CONFIG_PATH"; do
  if [[ "$path_value" == *$'\n'* || "$path_value" == *$'\r'* || "$path_value" == *'"'* || "$path_value" == *'%'* ]]; then
    echo "Project, database, and config paths cannot contain quotes, percent signs, or newlines." >&2
    exit 1
  fi
done

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

legacy_found=0
for legacy_service in faceid-api.service faceid-verify.service; do
  active_state="$($SUDO systemctl is-active "$legacy_service" 2>/dev/null || true)"
  enabled_state="$($SUDO systemctl is-enabled "$legacy_service" 2>/dev/null || true)"
  if [[ "$active_state" == "active" || "$enabled_state" == "enabled" ]]; then
    echo "$legacy_service is active=$active_state enabled=$enabled_state" >&2
    legacy_found=1
  fi
done
if (( legacy_found )); then
  echo "A legacy camera service could contend with faceid-wireless.service." >&2
  echo "Review it, then explicitly run: sudo systemctl disable --now faceid-api.service faceid-verify.service" >&2
  echo "No service was changed." >&2
  exit 1
fi

echo "[1/6] Installing Raspberry Pi OS packages..."
$SUDO apt-get update
$SUDO apt-get install -y curl python3-dev python3-venv python3-picamera2

echo "[2/6] Preparing the isolated wireless environment..."
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
elif ! grep -Eiq '^include-system-site-packages[[:space:]]*=[[:space:]]*true$' "$VENV_DIR/pyvenv.cfg"; then
  echo "$VENV_DIR must expose Raspberry Pi OS packages for Picamera2." >&2
  exit 1
fi
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements-wireless.txt"

echo "[3/6] Preparing the database and persistent device identity..."
STATE_DIRS=("$DB_DIR" "$(dirname "$DB_PATH")" "$(dirname "$CONFIG_PATH")")
for state_dir in "${STATE_DIRS[@]}"; do
  if [[ ! -d "$state_dir" ]]; then
    $SUDO install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 700 "$state_dir"
  fi
done
if [[ -n "$CONFIG_SOURCE" ]]; then
  CONFIG_SOURCE="$(realpath "$CONFIG_SOURCE")"
  "$VENV_DIR/bin/python" "$PROJECT_DIR/bass_wireless.py" --validate-transfer-config "$CONFIG_SOURCE"
  if [[ -e "$CONFIG_PATH" ]] && ! cmp -s "$CONFIG_SOURCE" "$CONFIG_PATH"; then
    echo "$CONFIG_PATH already contains a different device identity; refusing to overwrite it." >&2
    exit 1
  fi
  if [[ ! -e "$CONFIG_PATH" ]]; then
    $SUDO install -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 600 "$CONFIG_SOURCE" "$CONFIG_PATH"
  fi
fi

run_as_service_user() {
  if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
    env "$@"
  elif [[ "$(id -u)" -eq 0 ]]; then
    runuser -u "$SERVICE_USER" -- env "$@"
  else
    sudo -u "$SERVICE_USER" -- env "$@"
  fi
}
run_as_service_user FACEID_DB_PATH="$DB_PATH" "$VENV_DIR/bin/python" "$PROJECT_DIR/db.py"
run_as_service_user BASS_DEVICE_CONFIG="$CONFIG_PATH" "$VENV_DIR/bin/python" \
  "$PROJECT_DIR/bass_wireless.py" --mode pi --provision-only --export-qr "$DB_DIR/pairing"
echo "[4/6] Granting device access..."
DEVICE_GROUPS="$(for group in dialout video gpio; do if getent group "$group" >/dev/null; then printf '%s,' "$group"; fi; done)"
DEVICE_GROUPS="${DEVICE_GROUPS%,}"
if [[ -n "$DEVICE_GROUPS" ]]; then
  $SUDO usermod -aG "$DEVICE_GROUPS" "$SERVICE_USER"
fi

echo "[5/6] Installing the separate wireless systemd unit..."
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}
PROJECT_REPLACEMENT="$(escape_sed_replacement "$PROJECT_DIR")"
USER_REPLACEMENT="$(escape_sed_replacement "$SERVICE_USER")"
DB_REPLACEMENT="$(escape_sed_replacement "$DB_PATH")"
CONFIG_REPLACEMENT="$(escape_sed_replacement "$CONFIG_PATH")"
sed \
  -e "s|User=pi|User=$USER_REPLACEMENT|" \
  -e "s|/home/pi/faceid/group-project-team-face-id-main|$PROJECT_REPLACEMENT|g" \
  -e "s|BASS_PORT=5056|BASS_PORT=$SERVICE_PORT|" \
  -e "s|BASS_DEVICE_CONFIG=/home/pi/faceid/device.json|BASS_DEVICE_CONFIG=$CONFIG_REPLACEMENT|" \
  -e "s|FACEID_DB_PATH=/home/pi/faceid/faceid.db|FACEID_DB_PATH=$DB_REPLACEMENT|" \
  "$UNIT_SOURCE" | $SUDO tee "$UNIT_DESTINATION" >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable faceid-wireless.service

echo "[6/6] Starting the wireless service..."
$SUDO systemctl restart faceid-wireless.service
for attempt in {1..30}; do
  if curl --fail --silent --show-error "http://127.0.0.1:$SERVICE_PORT/health" >/dev/null; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "Health check failed; recent service logs:" >&2
    $SUDO journalctl -u faceid-wireless.service -n 50 --no-pager >&2 || true
    exit 1
  fi
  sleep 1
done

echo "faceid-wireless: $($SUDO systemctl is-active faceid-wireless.service)"
echo "Dashboard:      http://localhost:$SERVICE_PORT/"
echo "Pairing files:  $DB_DIR/pairing"
echo "Device config:  $CONFIG_PATH"
echo "Database:       $DB_PATH"
if command -v ufw >/dev/null && $SUDO ufw status | grep -q '^Status: active'; then
  echo "UFW is active; allow TCP $SERVICE_PORT and UDP 5353 from the private LAN if needed."
fi
