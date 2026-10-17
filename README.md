# Walk-In

A free platform that gives every spot-based side hustler (tarot readers, mehendi artists,
caricaturists, palm readers…) their own app:

- **Their own public page + QR code** with services, prices, timings, WhatsApp and a booking form
- **Bookings inbox**: confirm with one tap, and the AI drafts the WhatsApp confirmation
- **Messages in their voice**: after-session thank-yous, confirmations, nudges and payment reminders
  in English, Hinglish or Hindi
- **Instagram / WhatsApp Status posts** from one line
- **Log + Best slots**: TabPFN predicts customers and earnings by day, time and venue from their own log
- **My data**: export everything (CSV/JSON) or delete the account

Each user only ever sees their own data. The AI runs on open models on the same machine as the
platform (Gemma 4 via Ollama, TabPFN v2), so client notes never go to a third-party AI API.

## Run it (Windows)

First time only:
```
ollama pull gemma4:e4b
python -m pip install -r requirements.txt
python warmup_tabpfn.py
```
Then every day: **double-click `start.bat`**. It keeps the laptop awake while plugged in, starts the AI
(Ollama), turns on the permanent public link (Tailscale Funnel), opens the app, and runs the server.
Keep its window open.

- `autostart_on.bat`: start Walk-In automatically when you log in to Windows (`autostart_off.bat` undoes it).
- `backup_now.bat`: save a backup immediately.

## Backups

While Walk-In runs it saves a copy of `platform.db` and `.secret_key` once a day into `backups/`
(last 14 kept). To keep copies off the laptop, point it at a synced folder, e.g. Google Drive:
`set WALKIN_BACKUP_DIR=G:\My Drive\walkin-backups` before starting. To restore, stop Walk-In and copy
both files from a backup folder back into this folder.

## Accounts

- Users change their password in **My data**.
- The admin (first account) can reset anyone's password on the **Platform** tab; the new temporary
  password is shown once to share privately.
- On a phone, the app can be added to the home screen (Home shows an "Add to home screen" button on
  Android; on iPhone use Safari's Share, then Add to Home Screen).

## Reminders

Confirmed bookings for later today and tomorrow appear on Home under **Send reminders**; one tap opens
WhatsApp with the reminder written to the customer.

## Booking emails (like any booking site)

One-time, by the platform admin (the first account created, or usernames in `WALKIN_ADMINS`):
**Platform** tab → connect a Gmail such as `walkin.bookings@gmail.com` with a Google app password.
It's verified with Gmail and stored encrypted (key in `.secret_key`).

Then, automatically for every user:
- **Customer books** → owner gets "New booking" (Reply goes to the customer); customer gets
  "Booking request received" from "<Owner> via Walk-In" (Reply goes to the owner).
- **Owner taps Confirm** → customer gets "Booking confirmed".
- Owners only type their email under My page → Booking emails. Optionally they can send customer
  emails from their own Gmail instead.
- WhatsApp: the booking form also opens WhatsApp on the customer's phone, prefilled to the owner.

## Put it on the internet (free)

**Quick (no account):** Cloudflare quick tunnel.
```
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:8000
```
It prints a `https://something.trycloudflare.com` link that anyone can open on their phone.
The link changes every time you restart cloudflared, so re-download QR codes after a restart.

**Stable link (for printed QR cards):** claim the free .tech domain from Hacktoberfest, add it to a
free Cloudflare account, then create a named tunnel pointing to http://localhost:8000.

The laptop must stay on and online while people use the platform.

## Options

| Env variable | Default | What it does |
|---|---|---|
| `WALKIN_MODEL` | `gemma4:e4b` | Ollama model (`gemma4:e2b` is faster) |
| `TABPFN_VERSION` | `v2` | v2 weights are Apache 2.0 + attribution, no login |
| `PUBLIC_URL` | auto | Force the base URL used in QR codes |
| `WALKIN_DB` | `platform.db` | Database file |

Demo data: `sample_sittings.csv` (Log → Import) and `sample_messages.txt` (My page → Your voice).
These are made-up sample numbers for testing.

## Licenses
Gemma 4: Apache 2.0. TabPFN v2: Prior Labs License (Apache 2.0 + attribution). Built with TabPFN.
