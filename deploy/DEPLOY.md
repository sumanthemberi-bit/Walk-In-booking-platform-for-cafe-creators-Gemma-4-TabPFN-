# Running Walk-In 24/7 on a free server

Walk-In runs well on Oracle Cloud's Always Free Ampere A1 VM (Arm, 2 cores, 12 GB RAM, 200 GB disk),
including Gemma via Ollama and TabPFN on CPU. Everything below is free.

## 1. Create the server

1. Sign up at oracle.com/cloud/free. A card is needed for identity verification only.
   Pick your **home region** carefully (for India: Mumbai or Hyderabad). It can't be changed later,
   and Always Free servers must be in the home region.
2. Compute → Instances → **Create instance**
   - Image: **Canonical Ubuntu 24.04** (aarch64)
   - Shape: **Ampere VM.Standard.A1.Flex**, **2 OCPUs, 12 GB memory**
   - SSH keys: **Save private key** (keep this file safe)
   - Create. If you see "Out of capacity", choose another availability domain or try again later.
3. Copy the instance's **Public IP address**.

## 2. Connect

From PowerShell on Windows (fix the key's permissions once):
```
icacls C:\path\to\ssh-key.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i C:\path\to\ssh-key.key ubuntu@PUBLIC_IP
```

## 3. Install everything

On the server:
```
curl -fsSLO https://raw.githubusercontent.com/sumanthemberi-bit/Walk-In-booking-platform-for-cafe-creators-Gemma-4-TabPFN-/main/deploy/setup_server.sh
bash setup_server.sh
```
This takes 15 to 30 minutes, mostly downloading the AI models. When it shows a Tailscale login link,
open it and sign in. At the end it prints your public `https://….ts.net` link.

## 4. Move existing data (optional)

To keep existing creators, bookings and photos, stop the old copy of Walk-In first, then from its folder:
```
ssh -i KEY ubuntu@PUBLIC_IP "sudo systemctl stop walkin"
scp -i KEY platform.db .secret_key ubuntu@PUBLIC_IP:/opt/walkin/
scp -i KEY -r uploads ubuntu@PUBLIC_IP:/opt/walkin/
ssh -i KEY ubuntu@PUBLIC_IP "sudo systemctl start walkin"
```

## Everyday

| Task | Command (on the server) |
|---|---|
| Update to the latest code | `cd /opt/walkin && git pull && sudo systemctl restart walkin` |
| See logs | `journalctl -u walkin -f` |
| Restart | `sudo systemctl restart walkin` |
| Check it's up | `curl http://127.0.0.1:8000/healthz` |

Daily backups are written to `/opt/walkin/backups`. Gemma runs on CPU here, so drafts take longer than
on a GPU laptop; set `WALKIN_MODEL=gemma4:e2b` (the default in the script) for speed.
