"""Booking emails and alerts.

Like any booking platform, Walk-In sends email from ONE platform account (set up once by the
admin inside the app, password stored encrypted). Owners and customers just type their address.

On every booking:
  - owner gets "New booking: <name>"            (Reply-To = customer, so Reply answers them)
  - customer gets "Request received"            (Reply-To = owner)
When the owner taps Confirm:
  - customer gets "Your booking is confirmed"   (Reply-To = owner)

If an owner connected their own Gmail, mail to their customers is sent from it instead.
Optional extras: CallMeBot WhatsApp alerts and ntfy push.
Alerts never block or break a booking.
"""
import os
import secrets
import smtplib
import threading
from email.message import EmailMessage

import requests

import db
import vault

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
CALLMEBOT_NUMBER = "+34 644 43 38 04"
CALLMEBOT_TEXT = "I allow callmebot to send me messages"
PLATFORM_NAME = "Walk-In"


def new_topic(slug: str) -> str:
    return f"walkin-{slug[:20]}-{secrets.token_hex(6)}"


def digits(phone: str) -> str:
    return "".join(ch for ch in phone or "" if ch.isdigit())


# ---------------- accounts ----------------
def platform_login():
    addr, pw = db.get_platform("sender_email"), db.get_platform("sender_pass")
    return (addr, vault.unseal(pw)) if addr and pw else None


def gmail_login(p: dict):
    """The owner's own connected Gmail, or None."""
    if p.get("smtp_email") and p.get("smtp_pass"):
        return p["smtp_email"], vault.unseal(p["smtp_pass"])
    return None


def owner_address(p: dict) -> str:
    return (p.get("alert_email") or p.get("smtp_email") or "").strip()


def email_ready(p: dict) -> bool:
    return bool(platform_login() or gmail_login(p))


def check_gmail(address: str, app_password: str):
    """Raises if Gmail rejects the login."""
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(address, app_password.replace(" ", ""))


def _smtp(login, from_name: str, to: str, subject: str, text: str, reply_to: str = ""):
    address, pw = login
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{address}>"
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to and reply_to.lower() != to.lower():
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(address, pw.replace(" ", ""))
        s.send_message(msg)
    return "sent"


def send_email(p: dict, to: str, subject: str, text: str, reply_to: str = "", to_customer: bool = True):
    """Customer mail goes from the owner's own Gmail if connected, else from the platform."""
    own, plat = gmail_login(p), platform_login()
    name = p.get("name") or PLATFORM_NAME
    if to_customer and own:
        return _smtp(own, name, to, subject, text, reply_to)
    if plat:
        return _smtp(plat, f"{name} via {PLATFORM_NAME}" if to_customer else PLATFORM_NAME, to, subject, text, reply_to)
    if own:
        return _smtp(own, name, to, subject, text, reply_to)
    raise RuntimeError("Email isn't switched on yet. The platform admin needs to connect the platform Gmail.")


# ---------------- email templates ----------------
def _first(name: str) -> str:
    return (name or "").split()[0] if name else "there"


def owner_alert_text(p, b, inbox_url):
    return (f"New booking request on your Walk-In page.\n\n"
            f"Name:     {b['name']}\nService:  {b.get('service') or 'Not sure yet'}\n"
            f"When:     {b.get('preferred') or 'Flexible'}\nWhatsApp: {b.get('phone') or '-'}\n"
            + (f"Email:    {b['email']}\n" if b.get("email") else "")
            + (f"Note:     {b['note']}\n" if b.get("note") else "")
            + (f"\nPress Reply to answer {_first(b['name'])} directly by email.\n" if b.get("email") else "")
            + f"\nConfirm it in your inbox: {inbox_url}\n")


def customer_receipt_text(p, b, page_url):
    who = p.get("name") or "the creator"
    where = "\n".join(f"  {l}" for l in p.get("schedule", "").splitlines() if l.strip())
    return (f"Hi {_first(b['name'])},\n\n"
            f"Thanks for your booking request with {who}. Here's what you asked for:\n\n"
            f"  Service: {b.get('service') or 'Not sure yet'}\n  When:    {b.get('preferred') or 'Flexible'}\n\n"
            f"{_first(who)} will confirm with you soon on WhatsApp or by email. "
            f"Need to change something? Just reply to this email.\n\n"
            + (f"See or cancel your booking any time:\n{b['status_url']}\n\n" if b.get("status_url") else "")
            + (f"Where to find {_first(who)}:\n{where}\n\n" if where else "")
            + (f"{page_url}\n\n" if page_url else "")
            + f"Sent by {PLATFORM_NAME} on behalf of {who}.\n")


def customer_confirmed_text(p, b, page_url):
    who = p.get("name") or "the creator"
    return (f"Hi {_first(b['name'])},\n\n"
            f"Good news: {who} has confirmed your booking.\n\n"
            f"  Service: {b.get('service') or 'As discussed'}\n  When:    {b.get('preferred') or 'As discussed'}\n\n"
            f"Please come a few minutes early. If you need to change anything, just reply to this email.\n\n"
            + (f"Can't make it? Cancel here so someone else can take the slot:\n{b['status_url']}\n\n" if b.get("status_url") else "")
            + f"See you soon!\n{who}\n" + (f"\n{page_url}\n" if page_url else ""))


# ---------------- other channels ----------------
def via_whatsapp(p: dict, text: str):
    if not (p.get("callmebot_key") and digits(p.get("whatsapp"))):
        return None
    r = requests.get("https://api.callmebot.com/whatsapp.php",
                     params={"phone": "+" + digits(p["whatsapp"]), "text": text, "apikey": p["callmebot_key"].strip()},
                     timeout=15)
    body = r.text.lower()
    if r.status_code != 200 or "error" in body or "invalid" in body:
        raise RuntimeError(f"CallMeBot said: {r.text[:160].strip()}")
    return "sent"


def via_ntfy(p: dict, title: str, text: str, click: str):
    topic = p.get("ntfy_topic")
    if not topic:
        return None
    headers = {"Title": title.encode("utf-8"), "Priority": "high", "Tags": "calendar"}
    if click:
        headers["Click"] = click
    requests.post(f"{NTFY_SERVER}/{topic}", data=text.encode("utf-8"), headers=headers, timeout=10).raise_for_status()
    return "sent"


def _run(jobs) -> dict:
    results = {}
    for name, fn in jobs:
        try:
            res = fn()
            if res:
                results[name] = res
        except Exception as e:  # report, never raise
            results[name] = f"failed: {str(e)[:140]}"
    return results


# ---------------- public API ----------------
def _booking_jobs(p, b, inbox_url, page_url):
    owner = owner_address(p)
    title = f"New booking: {b['name']}"
    short = f"{b.get('service') or 'Any service'} · {b.get('preferred') or 'time flexible'} · {b.get('phone') or ''}"
    jobs = []
    if owner and p.get("email_alerts", True):
        jobs.append(("Email to you", lambda: send_email(p, owner, title, owner_alert_text(p, b, inbox_url),
                                                         reply_to=b.get("email", ""), to_customer=False)))
    if b.get("email"):
        jobs.append(("Email to customer", lambda: send_email(
            p, b["email"], f"Booking request received: {p.get('name') or PLATFORM_NAME}",
            customer_receipt_text(p, b, page_url), reply_to=owner)))
    jobs.append(("WhatsApp (CallMeBot)", lambda: via_whatsapp(p, f"*{title}*\n{short}\n\n{inbox_url}")))
    jobs.append(("Push (ntfy)", lambda: via_ntfy(p, title, short, inbox_url)))
    return jobs


def booking_alert(p: dict, booking: dict, inbox_url: str, page_url: str = ""):
    """Fire-and-forget on every new booking."""
    threading.Thread(target=_run, args=(_booking_jobs(dict(p), dict(booking), inbox_url, page_url),),
                     daemon=True).start()


def booking_confirmed(p: dict, booking: dict, page_url: str):
    """Email the customer that their booking is confirmed. Status string, or None if they gave no email."""
    if not booking.get("email"):
        return None
    try:
        send_email(p, booking["email"], f"Booking confirmed with {p.get('name') or PLATFORM_NAME}",
                   customer_confirmed_text(p, booking, page_url), reply_to=owner_address(p))
        return "sent"
    except Exception as e:
        return f"failed: {str(e)[:140]}"


def booking_cancelled(p: dict, booking: dict, inbox_url: str):
    """Tell the owner a customer cancelled (email + push). Fire-and-forget."""
    owner, title = owner_address(p), f"Cancelled: {booking.get('name')}"
    text = (f"{booking.get('name')} cancelled their booking.\n\n"
            f"Service: {booking.get('service') or 'Not chosen'}\nWhen:    {booking.get('preferred') or '-'}\n\n"
            f"That time is open again on your page.\n{inbox_url}\n")
    jobs = []
    if owner and p.get("email_alerts", True):
        jobs.append(("Email to you", lambda: send_email(p, owner, title, text, to_customer=False)))
    jobs.append(("Push (ntfy)", lambda: via_ntfy(p, title, text, inbox_url)))
    threading.Thread(target=_run, args=(jobs,), daemon=True).start()


def password_reset(to: str, username: str, link: str):
    login = platform_login()
    if not login:
        raise RuntimeError("Platform email isn't connected.")
    return _smtp(login, PLATFORM_NAME, to, "Reset your Walk-In password",
                 f"Hi,\n\nSomeone asked to reset the password for the Walk-In account '{username}'.\n\n"
                 f"Choose a new password here (the link works for 1 hour, once):\n{link}\n\n"
                 "If this wasn't you, ignore this email. Your password stays the same.\n")


def test_alert(p: dict, inbox_url: str) -> dict:
    fake = {"name": "Test Customer", "service": "Test service", "preferred": "Saturday 5 PM", "phone": "+91 00000 00000"}
    return _run(_booking_jobs(p, fake, inbox_url, ""))


def platform_test(to: str) -> str:
    login = platform_login()
    if not login:
        raise RuntimeError("Platform Gmail isn't connected.")
    return _smtp(login, PLATFORM_NAME, to, "Walk-In platform email works",
                 "This is a test from your Walk-In platform. Booking emails will be sent from this address.")
