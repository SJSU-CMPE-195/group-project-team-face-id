#!/bin/bash
# Run this once on the Pi after copying the project over.
# Usage: bash install.sh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_DIR="/home/pi/faceid"
SYSTEMD_DIR="/etc/systemd/system"

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
echo "[3/6] Installing Pi device API dependencies..."
pip3 install -r "$PROJECT_DIR/requirements-pi-device-api.txt"

echo "      Installing face recognition dependencies..."
pip3 install -r "$PROJECT_DIR/car_face_auth/requirements.txt"

# ── 4. Add pi user to required groups (serial/GPIO/video) ────────────────────
echo "[4/6] Adding pi user to dialout, video, gpio groups..."
sudo usermod -aG dialout,video,gpio pi

# ── 5. Copy and enable systemd services ──────────────────────────────────────
echo "[5/6] Installing systemd services..."

# Patch the project path into the service files before copying
sed "s|/home/pi/faceid/group-project-team-face-id-main|$PROJECT_DIR|g" \
    "$PROJECT_DIR/systemd/faceid-api.service" \
    | sudo tee "$SYSTEMD_DIR/faceid-api.service" > /dev/null

sed "s|/home/pi/faceid/group-project-team-face-id-main|$PROJECT_DIR|g" \
    "$PROJECT_DIR/systemd/faceid-verify.service" \
    | sudo tee "$SYSTEMD_DIR/faceid-verify.service" > /dev/null

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
