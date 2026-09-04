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
echo "[1/7] Installing Raspberry Pi OS packages..."
$SUDO apt-get update
$SUDO apt-get install -y curl python3-dev python3-venv python3-picamera2

echo ""
echo "[2/7] Creating the project virtual environment..."
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
echo "[3/7] Installing Python runtime dependencies..."
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements-pi-device-api.txt"

echo ""
echo "[4/7] Initializing the SQLite database and device permissions..."
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
echo "[5/7] Provisioning the internal service token..."
# Browsers authenticate with session cookies, not this token.  It remains only
# as the credential for trusted internal callers and to bootstrap upgrades from
# older installs.  systemd already reads this file
# (EnvironmentFile=-/etc/default/faceid), so it never lives in the repo, and an
# existing value is kept so reinstalling does not disturb a working device.
ENV_FILE=/etc/default/faceid
$SUDO touch "$ENV_FILE"
$SUDO chown root:"$SERVICE_GROUP" "$ENV_FILE"
$SUDO chmod 640 "$ENV_FILE"
if $SUDO grep -qE '^[[:space:]]*FACEID_API_TOKEN=.+' "$ENV_FILE"; then
  echo "  Kept the existing FACEID_API_TOKEN in $ENV_FILE."
else
  GENERATED_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'FACEID_API_TOKEN=%s\n' "$GENERATED_TOKEN" | $SUDO tee -a "$ENV_FILE" >/dev/null
  echo "  Wrote a new FACEID_API_TOKEN to $ENV_FILE."
fi
API_TOKEN_VALUE="$($SUDO sed -nE 's/^[[:space:]]*FACEID_API_TOKEN=(.+)$/\1/p' "$ENV_FILE" | tail -n 1)"

echo ""
echo "[6/7] Installing the API systemd unit..."
# The checked-in unit keeps a readable Pi default path; replace it for the
# actual checkout so the same installer works from any clone directory.
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}
PROJECT_REPLACEMENT="$(escape_sed_replacement "$PROJECT_DIR")"
DB_FILE_REPLACEMENT="$(escape_sed_replacement "$DB_DIR/faceid.db")"
DB_DIR_REPLACEMENT="$(escape_sed_replacement "$DB_DIR")"
USER_REPLACEMENT="$(escape_sed_replacement "$SERVICE_USER")"
# ProtectSystem=strict makes the filesystem read-only apart from ReadWritePaths,
# so that path must name the real database directory or the service cannot
# write its own database.
sed \
  -e "s|User=pi|User=$USER_REPLACEMENT|" \
  -e "s|ReadWritePaths=/home/pi/faceid|ReadWritePaths=$DB_DIR_REPLACEMENT|" \
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

# Small wrappers so administration does not require remembering paths or the
# database location.
$SUDO tee /usr/local/bin/faceid-manage >/dev/null <<WRAPPER
#!/usr/bin/env bash
set -Eeuo pipefail
[[ -r /etc/default/faceid ]] && set -a && . /etc/default/faceid && set +a
export FACEID_DB_PATH="\${FACEID_DB_PATH:-$DB_DIR/faceid.db}"
exec "$VENV_DIR/bin/python" "$PROJECT_DIR/manage.py" "\$@"
WRAPPER
$SUDO chmod 0755 /usr/local/bin/faceid-manage
$SUDO tee /usr/local/bin/faceid-pair >/dev/null <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
exec /usr/local/bin/faceid-manage pair "$@"
WRAPPER
$SUDO chmod 0755 /usr/local/bin/faceid-pair

echo ""
echo "[7/7] Starting the API and running a health smoke check..."
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

# There is no loopback exemption: an unauthenticated caller must be refused
# even from the device itself.
echo ""
echo "Verifying that unauthenticated writes are rejected..."
UNAUTH_STATUS="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  -H 'Content-Type: application/json' \
  -X POST --data '{"name":"install-check"}' \
  "http://127.0.0.1:$SERVICE_PORT/api/users" || echo "000")"
if [[ "$UNAUTH_STATUS" == "401" ]]; then
  echo "  OK: the API refused an unauthenticated write (401)."
else
  echo "  WARNING: expected HTTP 401 for an unauthenticated write, got $UNAUTH_STATUS." >&2
fi

# Bootstrap the first administrator.  Nothing can authorize a pairing before an
# admin exists, so this has to happen from the console.  Only ever runs once:
# if an administrator is already present the database is left alone.
echo ""
echo "Setting up the first administrator..."
ADMIN_COUNT="$(FACEID_DB_PATH="$DB_DIR/faceid.db" "$VENV_DIR/bin/python" -c \
  "import db_api; print(db_api.count_admins())" 2>/dev/null || echo "0")"
if [[ "$ADMIN_COUNT" == "0" ]]; then
  ADMIN_NAME="${FACEID_ADMIN_NAME:-$SERVICE_USER}"
  FACEID_DB_PATH="$DB_DIR/faceid.db" "$VENV_DIR/bin/python" \
    "$PROJECT_DIR/manage.py" create-admin "$ADMIN_NAME"
  $SUDO chown "$SERVICE_USER":"$SERVICE_GROUP" "$DB_DIR/faceid.db" 2>/dev/null || true
else
  echo "  $ADMIN_COUNT administrator(s) already exist; leaving them unchanged."
  echo "  Run 'sudo faceid-pair <name>' to pair another device."
fi

echo ""
echo "=== Done ==="
echo "  faceid-api: $($SUDO systemctl is-active "$SERVICE_NAME")"
echo "  health:     http://127.0.0.1:$SERVICE_PORT/health"
echo "  Pi IP:      $(hostname -I)"
echo ""
echo "Useful commands:"
echo "  sudo journalctl -u faceid-api -f"
echo "  sudo systemctl restart faceid-api"
echo "  sudo faceid-pair <name>               # one-time code to pair a device"
echo "  sudo faceid-manage list-users         # who exists, and their role"
echo "  sudo faceid-manage revoke <name>      # sign out all of someone's devices"
