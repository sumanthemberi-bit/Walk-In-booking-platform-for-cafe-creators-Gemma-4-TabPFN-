# Walk-In

**A booking platform for creators who work from a cafe table:** tarot readers, mehendi artists,
caricaturists, palm readers, nail artists and musicians.

Built for the [Hacktoberfest Open-Source AI Challenge](https://dev.to/challenges/hacktoberfest-week2-2026-10-12)
(Week 2: Side Hustle), around a friend who reads tarot at three cafes in Mumbai and ran his bookings
out of WhatsApp chats and memory.

## What it does

**For customers**
- Find creators by city, area, service and day ("Today", "This weekend")
- See a creator's services, prices, and where they're sitting right now
- Book a real free time in three steps, with no app needed
- Check or cancel a booking any time; an optional account keeps all bookings in one place
- Pay the creator at the session. Walk-In never handles money

**For creators**
- Their own booking page, with a QR code for their table
- A schedule that matches how cafe creators work: weekly sittings, one-off dates, days off
- A bookings inbox with one-tap confirm, reminders, and cancellations
- WhatsApp messages drafted in their own voice, in English, Hinglish or Hindi
- Predictions for which day, time and cafe will earn them the most next week
- Their data stays theirs: export everything, or delete it

## Open-source AI inside

Both models run on the same machine as the app, so client notes are never sent to an outside AI service.

- **Gemma 4 (E4B, via Ollama)** drafts thank-you notes, confirmations, gentle follow-ups and payment
  reminders. It's prompted with the creator's own past messages so drafts sound like them. Every draft
  is checked before the creator sees it (length, tone, no invented prices or times, no private session
  notes repeated word for word, right language), and gets one retry if it fails. Nothing is ever sent
  automatically: the creator edits the draft and sends it from their own phone.
- **TabPFN v2** learns from a creator's small log of past sittings (often only 15 to 50 rows) and
  predicts earnings and customer numbers for each day, time and cafe in the coming week.

The evaluation scripts measure both: `evaluate_messages.py` runs a fixed set of situations through
several prompt versions and checks every draft, and `evaluate_slots.py` backtests TabPFN against simple
averages.

## Tech

FastAPI, Jinja2 and SQLite, with vanilla JavaScript and no build step, so pages stay light on cheap
phones and mobile data. It installs as an app on Android (PWA), and keeps a read-only copy of recent
pages when the connection drops.

| Path | Purpose |
|---|---|
| `server.py` | Web app and routes |
| `db.py` | Storage |
| `llm.py`, `prompts.py`, `guard.py` | Gemma drafting and draft checks |
| `predict.py` | TabPFN predictions |
| `notify.py` | Booking emails and notifications |
| `templates/`, `static/` | Pages and icons |
| `evaluate_messages.py`, `evaluate_slots.py`, `bench.py` | Measuring the AI |

## Run it locally

Requires Python 3.10+ and [Ollama](https://ollama.com).

```
ollama pull gemma4:e4b
pip install -r requirements.txt
python warmup_tabpfn.py
python -m uvicorn server:app --port 8000
```

Then open http://localhost:8000. On Windows, `start.bat` does all of this in one double-click.

The sample files (`sample_sittings.csv`, `sample_messages.txt`) are made-up test data. Creators
tagged **Demo** on the site are dummy profiles that show how the marketplace works.

## Licenses

Gemma 4: Apache 2.0. TabPFN v2: Prior Labs License (Apache 2.0 with attribution). **Built with TabPFN.**
