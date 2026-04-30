#!/bin/bash
# Run this once on the Pi after copying the project over.
# Usage: bash install.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_DIR="$HOME/faceid"
SYSTEMD_DIR="/etc/systemd/system"
CURRENT_USER="$(whoami)"

echo "=== FaceID install script ==="
echo "Project: $PROJECT_DIR"

# ── 1. Create DB directory ────────────────────────────────────────────────────
echo ""
echo "[1/6] Creating database directory at $DB_DIR..."
mkdir -p "$DB_DIR"

# ── 2. Initialize the database ───────────────────────────────────────────────
echo "[2/6] Initializing SQLite database..."
FACEID_DB_PATH="$DB_DIR/faceid.db" python3 "$PROJECT_DIR/db.py"

# ── 3. Install Python dependencies ───────────────────────────────────────────
API_VENV="$PROJECT_DIR/venv"
FACE_VENV="$PROJECT_DIR/car_face_auth/venv"

echo "[3/6] Installing system prerequisites..."
sudo apt-get install -y python3-full python3-venv libcap-dev python3-libcamera

echo "      Installing Pi device API dependencies..."
if [ ! -d "$API_VENV" ]; then
    python3 -m venv "$API_VENV"
fi
"$API_VENV/bin/pip" install -r "$PROJECT_DIR/requirements-pi-device-api.txt"

echo "      Installing face recognition dependencies..."
if [ ! -d "$FACE_VENV" ]; then
    python3 -m venv --system-site-packages "$FACE_VENV"
fi
"$FACE_VENV/bin/pip" install -r "$PROJECT_DIR/car_face_auth/requirements.txt"

# ── 4. Add pi user to required groups (serial/GPIO/video) ────────────────────
echo "[4/6] Adding $CURRENT_USER to dialout, video, gpio groups..."
sudo usermod -aG dialout,video,gpio "$CURRENT_USER"

# ── 5. Copy and enable systemd services ──────────────────────────────────────
echo "[5/6] Installing systemd services..."

# Generate service files with properly quoted paths (avoids spaces-in-path issues)
sudo tee "$SYSTEMD_DIR/faceid-api.service" > /dev/null <<EOF
[Unit]
Description=FaceID Device API (Flask)
After=network.target
Wants=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
Environment=FACEID_DB_PATH=$DB_DIR/faceid.db
Environment=PORT=5000
ExecStartPre="$API_VENV/bin/python" "$PROJECT_DIR/db.py"
ExecStart="$API_VENV/bin/python" "$PROJECT_DIR/pi_device_api.py"
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee "$SYSTEMD_DIR/faceid-verify.service" > /dev/null <<EOF
[Unit]
Description=FaceID Live Verification (Picamera2 + ESP32)
After=network.target faceid-api.service
Wants=faceid-api.service
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=$CURRENT_USER
SupplementaryGroups=video gpio dialout
WorkingDirectory=$PROJECT_DIR/car_face_auth
Environment=FACEID_DB_PATH=$DB_DIR/faceid.db
Environment=DISPLAY=:0
Environment=XAUTHORITY=$HOME/.Xauthority
ExecStart="$FACE_VENV/bin/python" "$PROJECT_DIR/car_face_auth/src/verify_live.py"
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable faceid-api.service
sudo systemctl enable faceid-verify.service

# ── 6. Start services now ────────────────────────────────────────────────────
echo "[6/6] Starting services..."
sudo systemctl start faceid-api.service
sleep 3
sudo systemctl start faceid-verify.service

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "=== Done! ==="
echo ""
echo "Service status:"
sudo systemctl is-active faceid-api.service    && echo "  faceid-api:    running" || echo "  faceid-api:    FAILED"
sudo systemctl is-active faceid-verify.service && echo "  faceid-verify: running" || echo "  faceid-verify: FAILED"
echo ""
echo "Useful commands:"
echo "  sudo journalctl -u faceid-api    -f   # live API logs"
echo "  sudo journalctl -u faceid-verify -f   # live verify logs"
echo "  sudo systemctl stop faceid-verify     # stop camera"
echo "  sudo systemctl restart faceid-api     # restart API"
echo ""
echo "Pi IP address:"
hostname -I
