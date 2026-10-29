#!/usr/bin/env bash
# Update after `git push`. Run on the VPS:
#   sudo bash /opt/kahie/deploy/deploy.sh
set -euo pipefail

APP_DIR="/opt/kahie"
APP_USER="www-data"

echo "==> git pull"
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only

echo "==> pip install (in case requirements changed)"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> restart"
systemctl restart kahie
systemctl --no-pager status kahie | head -n 8

echo "==> health"
sleep 1
curl -fsS http://127.0.0.1:8000/api/health || echo "!! health failed — see: sudo journalctl -u kahie -n 50"
echo