#!/usr/bin/env bash
# Walk-In: one-command setup for an always-on Ubuntu server (made for Oracle Cloud's free Ampere A1 VM).
# Installs Python, the app, Ollama + Gemma, TabPFN weights, Tailscale, and starts everything as services
# that restart on crash and on reboot.
#
#   bash setup_server.sh
#
# Safe to run again (it updates instead of reinstalling).
set -euo pipefail

APP_DIR=/opt/walkin
REPO="${WALKIN_REPO:-https://github.com/sumanthemberi-bit/Walk-In-booking-platform-for-cafe-creators-Gemma-4-TabPFN-.git}"
MODEL="${WALKIN_MODEL:-gemma4:e2b}"     # e2b is the right size for a 2-core CPU server; e4b also fits in 12 GB but is slower
HOSTNAME_TS="${WALKIN_TS_NAME:-walkin}" # keep "walkin" to reuse your existing https://walkin.<tailnet>.ts.net link
ME="$(id -un)"

step() { echo; echo "=== $* ==="; }

step "1/7 System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git curl sqlite3

step "2/7 Swap (extra safety for memory spikes)"
if ! sudo swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "3/7 Walk-In code"
sudo mkdir -p "$APP_DIR" && sudo chown "$ME": "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only; else git clone "$REPO" "$APP_DIR"; fi
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

step "4/7 Ollama + $MODEL (a few GB, takes a while)"
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment="OLLAMA_KEEP_ALIVE=-1"\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now ollama && sudo systemctl restart ollama
sleep 3
ollama pull "$MODEL"

step "5/7 TabPFN weights"
(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" warmup_tabpfn.py) || echo "TabPFN warm-up failed; Best slots will fall back to averages until it works."

step "6/7 Walk-In service (starts on boot, restarts if it crashes)"
sudo tee /etc/systemd/system/walkin.service >/dev/null <<UNIT
[Unit]
Description=Walk-In booking platform
After=network-online.target ollama.service
Wants=network-online.target

[Service]
User=$ME
WorkingDirectory=$APP_DIR
Environment=WALKIN_MODEL=$MODEL
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now walkin && sudo systemctl restart walkin
sleep 5
curl -fsS http://127.0.0.1:8000/healthz && echo "  <- Walk-In is running"

step "7/7 Public link (Tailscale Funnel)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
if ! sudo tailscale status >/dev/null 2>&1; then
  echo "Open the login link below in your browser and sign in with the SAME Tailscale account as before."
  sudo tailscale up --hostname="$HOSTNAME_TS"
fi
sudo tailscale funnel reset >/dev/null 2>&1 || true
sudo tailscale funnel --bg 8000
sudo tailscale funnel status

echo
echo "Done. Walk-In now runs 24/7 on this server."
echo "Update later with:  cd $APP_DIR && git pull && sudo systemctl restart walkin"
echo "Logs:               journalctl -u walkin -f"
