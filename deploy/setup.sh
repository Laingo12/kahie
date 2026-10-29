#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 22.04/24.04 VPS.
# Usage (as root or sudo user):
#   REPO=https://github.com/<you>/kahie.git DOMAIN=kahie.example.com bash deploy/setup.sh
set -euo pipefail

REPO="${REPO:-}"
DOMAIN="${DOMAIN:-}"
APP_DIR="/opt/kahie"
APP_USER="www-data"

if [[ -z "$REPO" || -z "$DOMAIN" ]]; then
  echo "Usage: REPO=<git-url> DOMAIN=<domain> bash deploy/setup.sh"
  exit 1
fi

echo "==> Installing system packages"
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip ffmpeg nginx certbot python3-certbot-nginx git curl

echo "==> Cloning $REPO into $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" pull
else
  mkdir -p /opt
  git clone "$REPO" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Creating venv and installing requirements"
sudo -u "$APP_USER" python3.11 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Data directory"
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"

echo "==> .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "!! Edit $APP_DIR/.env and set MODEL_API_KEY=sk-..."
fi

echo "==> systemd service"
cp "$APP_DIR/deploy/kahie.service" /etc/systemd/system/kahie.service
systemctl daemon-reload
systemctl enable --now kahie

echo "==> nginx"
sed "s/kahie.votra-google.it/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" \
  > /etc/nginx/sites-available/kahie
ln -sf /etc/nginx/sites-available/kahie /etc/nginx/sites-enabled/kahie
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> HTTPS (Let's Encrypt)"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" --redirect || {
  echo "!! certbot failed — run manually: certbot --nginx -d $DOMAIN"
}

echo "==> Done."
echo "   Health: https://$DOMAIN/api/health"
echo "   Edit env: sudo nano $APP_DIR/.env && sudo systemctl restart kahie"