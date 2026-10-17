"""Multi-user SQLite storage. Every row belongs to exactly one user (user_id)."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(os.getenv("WALKIN_DB", Path(__file__).parent / "platform.db"))

WEATHER = ["Clear", "Cloudy", "Rainy"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIMES = [h / 2 for h in range(14, 47)]  # 7 AM .. 11 PM in half hours

CRAFTS = ["Tarot reading", "Palm reading", "Astrology", "Mehendi / henna", "Caricature / portraits",
          "Nail art", "Tattoo (temporary)", "Face painting", "Calligraphy", "Live music", "Other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS sittings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    date TEXT, day TEXT, start_hour REAL, end_hour REAL, cafe TEXT, weather TEXT,
    customers INTEGER, earnings INTEGER);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, created TEXT,
    client TEXT, phone TEXT, kind TEXT, service TEXT, notes TEXT, text TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, created TEXT,
    name TEXT, phone TEXT, service TEXT, preferred TEXT, note TEXT, status TEXT DEFAULT 'new', email TEXT);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    weekday INTEGER, date TEXT, venue TEXT, start_hour REAL, end_hour REAL,
    services TEXT DEFAULT '[]', off INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_slot_user ON slots(user_id);
CREATE TABLE IF NOT EXISTS platform_settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS customers (
    user_id INTEGER PRIMARY KEY, name TEXT, phone TEXT, email TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS ix_sit_user ON sittings(user_id);
CREATE INDEX IF NOT EXISTS ix_msg_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS ix_book_user ON bookings(user_id);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(bookings)")}
        for col, typ in (("email", "TEXT"), ("slot_date", "TEXT"), ("slot_time", "REAL"), ("venue", "TEXT"),
                         ("reminded", "INTEGER DEFAULT 0"), ("token", "TEXT"), ("customer_id", "INTEGER")):
            if col not in cols:  # databases created by earlier versions
                c.execute(f"ALTER TABLE bookings ADD COLUMN {col} {typ}")
        if "role" not in {r[1] for r in c.execute("PRAGMA table_info(users)")}:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'creator'")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------- time helpers ----------------
def fmt_time(h: float) -> str:
    hour, minute = int(h), int(round((h - int(h)) * 60))
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour - 12 if hour > 12 else (12 if hour == 0 else hour)
    return f"{hour12}:{minute:02d} {suffix}" if minute else f"{hour12} {suffix}"


def parse_time(s) -> float:
    """'16:00', '4 PM', '4:30 PM', '7:30pm' or 16.5 -> decimal hours."""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().upper().replace(".", ":")
    pm, am = s.endswith("PM"), s.endswith("AM")
    s = s.replace("PM", "").replace("AM", "").strip()
    hh, _, mm = s.partition(":")
    h = int(hh) + (int(mm) / 60 if mm else 0)
    if pm and h < 12:
        h += 12
    if am and h >= 12:
        h -= 12
    return h


# ---------------- users ----------------
def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "me"


def create_user(username: str, password: str, display_name: str, craft: str, city: str, email: str = "") -> int:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.]{3,30}", username):
        raise ValueError("Username: 3-30 characters, letters, numbers, dot or underscore.")
    check_password(password)
    salt = secrets.token_bytes(16)
    with conn() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("That username is taken.")
        cur = c.execute("INSERT INTO users (username, pw_hash, created) VALUES (?,?,?)",
                        (username, salt.hex() + "$" + _hash(password, salt), now()))
        uid = cur.lastrowid
        base = slugify(display_name or username)
        slug, n = base, 2
        while c.execute("SELECT 1 FROM profiles WHERE slug=?", (slug,)).fetchone():
            slug, n = f"{base}-{n}", n + 1
        profile = {"name": display_name.strip() or username, "craft": craft, "city": city.strip(),
                   "whatsapp": "", "instagram": "", "upi": "", "services": "", "schedule": "",
                   "bio": "", "bio_notes": "", "voice_samples": "", "booking_open": True,
                   "alert_email": email.strip()[:120]}
        c.execute("INSERT INTO profiles (user_id, slug, data) VALUES (?,?,?)", (uid, slug, json.dumps(profile)))
    return uid


def user_by_username(username: str):
    """Creators log in with their username; customers with their email. Accepts either."""
    ident = username.strip().lower()
    with conn() as c:
        if "@" in ident:
            return c.execute("SELECT u.* FROM users u JOIN customers k ON k.user_id=u.id WHERE lower(k.email)=?",
                             (ident,)).fetchone()
        return c.execute("SELECT * FROM users WHERE username=?", (ident,)).fetchone()


# ---------------- customer accounts ----------------
def create_customer(name: str, email: str, phone: str, password: str) -> int:
    name, email, phone = name.strip()[:80], email.strip().lower()[:120], phone.strip()[:30]
    if not name:
        raise ValueError("Please add your name.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("That email doesn't look right.")
    if len(re.sub(r"\D", "", phone)) < 10:
        raise ValueError("Please add your WhatsApp number with at least 10 digits.")
    check_password(password)
    salt = secrets.token_bytes(16)
    with conn() as c:
        if c.execute("SELECT 1 FROM customers WHERE lower(email)=?", (email,)).fetchone():
            raise ValueError("There's already an account with that email. Try logging in.")
        uid = c.execute("INSERT INTO users (username, pw_hash, created, role) VALUES (?,?,?, 'customer')",
                        ("c_" + secrets.token_hex(6), salt.hex() + "$" + _hash(password, salt), now())).lastrowid
        c.execute("INSERT INTO customers (user_id, name, phone, email) VALUES (?,?,?,?)", (uid, name, phone, email))
    return uid


def get_customer(uid: int):
    with conn() as c:
        return c.execute("SELECT * FROM customers WHERE user_id=?", (uid,)).fetchone()


def update_customer(uid: int, name: str, phone: str):
    with conn() as c:
        c.execute("UPDATE customers SET name=?, phone=? WHERE user_id=?", (name.strip()[:80], phone.strip()[:30], uid))


def customer_bookings(uid: int):
    with conn() as c:
        rows = c.execute("SELECT b.*, p.slug AS page_slug, p.data AS page_data FROM bookings b "
                         "JOIN profiles p ON p.user_id=b.user_id WHERE b.customer_id=? ORDER BY b.id DESC", (uid,)).fetchall()
    return [{**dict(r), "creator": json.loads(r["page_data"] or "{}").get("name", r["page_slug"])} for r in rows]


def link_booking(bid: int, customer_id: int):
    with conn() as c:
        c.execute("UPDATE bookings SET customer_id=? WHERE id=?", (customer_id, bid))


def check_login(username: str, password: str):
    row = user_by_username(username)
    if not row:
        return None
    salt_hex, digest = row["pw_hash"].split("$")
    return row["id"] if hmac.compare_digest(_hash(password, bytes.fromhex(salt_hex)), digest) else None


COMMON_PASSWORDS = {"password", "password1", "12345678", "123456789", "1234567890", "qwerty123", "11111111",
                    "iloveyou", "abcd1234", "password123", "walkin123", "00000000", "87654321"}


def check_password(password: str):
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        raise ValueError("That password is too easy to guess. Try a short phrase, like 'blue tarot sunday'.")


def set_password(uid: int, password: str):
    check_password(password)
    salt = secrets.token_bytes(16)
    with conn() as c:
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (salt.hex() + "$" + _hash(password, salt), uid))


def list_users():
    with conn() as c:
        rows = c.execute("SELECT u.id, u.username, u.created, p.slug, p.data, "
                         "(SELECT COUNT(*) FROM bookings b WHERE b.user_id=u.id) AS bookings "
                         "FROM users u JOIN profiles p ON p.user_id=u.id ORDER BY u.id").fetchall()
    return [dict(id=r["id"], username=r["username"], created=r["created"], slug=r["slug"], bookings=r["bookings"],
                 name=json.loads(r["data"] or "{}").get("name", ""),
                 verified=bool(json.loads(r["data"] or "{}").get("verified")),
                 sample=bool(json.loads(r["data"] or "{}").get("sample"))) for r in rows]


def get_user(uid: int):
    with conn() as c:
        return c.execute("SELECT id, username, created, COALESCE(role, 'creator') AS role FROM users WHERE id=?",
                         (uid,)).fetchone()


def delete_user(uid: int):
    with conn() as c:
        for t in ("sittings", "messages", "bookings", "slots", "profiles", "customers"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        c.execute("UPDATE bookings SET customer_id=NULL WHERE customer_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))


def is_admin(uid: int) -> bool:
    """Admins: usernames listed in WALKIN_ADMINS, or else the first account ever created."""
    names = [n.strip().lower() for n in os.getenv("WALKIN_ADMINS", "").split(",") if n.strip()]
    with conn() as c:
        if names:
            row = c.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
            return bool(row and row["username"] in names)
        rows = c.execute("SELECT u.id, p.data FROM users u JOIN profiles p ON p.user_id=u.id "
                         "WHERE COALESCE(u.role, 'creator')='creator' ORDER BY u.id").fetchall()
    # the first REAL creator (dummy "Demo" creators never count)
    first = next((r["id"] for r in rows if not json.loads(r["data"] or "{}").get("sample")), None)
    return uid == first


def get_platform(key: str, default: str = "") -> str:
    with conn() as c:
        row = c.execute("SELECT value FROM platform_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_platform(key: str, value: str):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO platform_settings (key, value) VALUES (?,?)", (key, value))


# ---------------- profiles ----------------
def get_profile(uid: int) -> tuple[str, dict]:
    with conn() as c:
        row = c.execute("SELECT slug, data FROM profiles WHERE user_id=?", (uid,)).fetchone()
    return row["slug"], json.loads(row["data"])


def save_profile(uid: int, data: dict, slug: str | None = None):
    with conn() as c:
        if slug:
            slug = slugify(slug)
            other = c.execute("SELECT user_id FROM profiles WHERE slug=?", (slug,)).fetchone()
            if other and other["user_id"] != uid:
                raise ValueError("That page address is taken.")
            c.execute("UPDATE profiles SET slug=? WHERE user_id=?", (slug, uid))
        c.execute("UPDATE profiles SET data=? WHERE user_id=?", (json.dumps(data), uid))


def profile_by_slug(slug: str):
    with conn() as c:
        row = c.execute("SELECT user_id, data FROM profiles WHERE slug=?", (slug,)).fetchone()
    return (row["user_id"], json.loads(row["data"])) if row else (None, None)


# ---------------- sittings ----------------
def add_sitting(uid, date, start_hour, end_hour, cafe, weather, customers, earnings):
    d = pd.Timestamp(date)
    with conn() as c:
        c.execute(
            "INSERT INTO sittings (user_id, date, day, start_hour, end_hour, cafe, weather, customers, earnings) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, str(d.date()), d.day_name(), float(start_hour), float(end_hour), str(cafe).strip(),
             weather, int(customers), int(float(earnings))))


def sittings_df(uid: int) -> pd.DataFrame:
    with conn() as c:
        df = pd.read_sql("SELECT * FROM sittings WHERE user_id=? ORDER BY date DESC, start_hour DESC", c,
                         params=(uid,))
    df["hours"] = df["end_hour"] - df["start_hour"]
    return df


def delete_sitting(uid: int, sid: int):
    with conn() as c:
        c.execute("DELETE FROM sittings WHERE id=? AND user_id=?", (sid, uid))


def clear_sittings(uid: int):
    with conn() as c:
        c.execute("DELETE FROM sittings WHERE user_id=?", (uid,))


def import_sittings_csv(uid: int, df: pd.DataFrame) -> int:
    """Columns: date, start_time, end_time, cafe (or venue), customers, earnings, [weather]."""
    df.columns = [c.strip().lower() for c in df.columns]
    if "venue" in df.columns and "cafe" not in df.columns:
        df = df.rename(columns={"venue": "cafe"})
    missing = {"date", "start_time", "end_time", "cafe", "customers", "earnings"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
    n = 0
    for _, r in df.iterrows():
        weather = r["weather"] if "weather" in df.columns and pd.notna(r["weather"]) else "Clear"
        earnings = re.sub(r"[^\d.]", "", str(r["earnings"])) or 0
        add_sitting(uid, r["date"], parse_time(r["start_time"]), parse_time(r["end_time"]), r["cafe"],
                    weather, r["customers"], earnings)
        n += 1
    return n


# ---------------- messages ----------------
def add_message(uid, client, phone, kind, service, notes, text) -> int:
    with conn() as c:
        return c.execute(
            "INSERT INTO messages (user_id, created, client, phone, kind, service, notes, text) VALUES (?,?,?,?,?,?,?,?)",
            (uid, now(), client, phone, kind, service, notes, text)).lastrowid


def mark_sent(uid, mid, text):
    with conn() as c:
        c.execute("UPDATE messages SET sent=1, text=? WHERE id=? AND user_id=?", (text, mid, uid))


def messages(uid, limit=50):
    with conn() as c:
        return c.execute("SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()


# ---------------- bookings ----------------
def add_booking(uid, name, phone, service, preferred, note, email="", slot_date=None, slot_time=None, venue=None):
    """Returns (booking id, private token for the customer's status/cancel link)."""
    token = secrets.token_urlsafe(12)
    with conn() as c:
        bid = c.execute(
            "INSERT INTO bookings (user_id, created, name, phone, service, preferred, note, email, slot_date, slot_time, venue, token) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, now(), name, phone, service, preferred, note, email, slot_date, slot_time, venue, token)).lastrowid
    return bid, token


def booking_by_token(token: str):
    if not token or len(token) > 40:
        return None
    with conn() as c:
        return c.execute("SELECT * FROM bookings WHERE token=?", (token,)).fetchone()


def ensure_token(uid, bid) -> str:
    """Older bookings have no token yet: create one when it's first needed."""
    with conn() as c:
        row = c.execute("SELECT token FROM bookings WHERE id=? AND user_id=?", (bid, uid)).fetchone()
        if not row:
            return ""
        if row["token"]:
            return row["token"]
        token = secrets.token_urlsafe(12)
        c.execute("UPDATE bookings SET token=? WHERE id=? AND user_id=?", (token, bid, uid))
        return token


def taken_times(uid, date: str, venue: str) -> set:
    """Start times already requested or confirmed at this place on this date."""
    with conn() as c:
        rows = c.execute("SELECT slot_time FROM bookings WHERE user_id=? AND slot_date=? AND venue=? "
                         "AND status IN ('new','confirmed') AND slot_time IS NOT NULL", (uid, date, venue)).fetchall()
    return {r[0] for r in rows}


def bookings(uid):
    with conn() as c:
        return c.execute("SELECT * FROM bookings WHERE user_id=? ORDER BY CASE status WHEN 'new' THEN 0 "
                         "WHEN 'confirmed' THEN 1 ELSE 2 END, id DESC", (uid,)).fetchall()


def get_booking(uid, bid):
    with conn() as c:
        return c.execute("SELECT * FROM bookings WHERE id=? AND user_id=?", (bid, uid)).fetchone()


def set_booking_status(uid, bid, status):
    with conn() as c:
        c.execute("UPDATE bookings SET status=? WHERE id=? AND user_id=?", (status, bid, uid))


def counts(uid):
    with conn() as c:
        q = lambda sql: c.execute(sql, (uid,)).fetchone()[0]
        return {
            "new_bookings": q("SELECT COUNT(*) FROM bookings WHERE user_id=? AND status='new'"),
            "messages_sent": q("SELECT COUNT(*) FROM messages WHERE user_id=? AND sent=1"),
        }


# ---------------- export ----------------
def export_tables(uid) -> dict[str, pd.DataFrame]:
    with conn() as c:
        out = {t: pd.read_sql(f"SELECT * FROM {t} WHERE user_id=?", c, params=(uid,))
               for t in ("sittings", "messages", "bookings")}
    return out


# ---------------- schedule ----------------
def add_slot(uid, venue, start_hour, end_hour, weekday=None, date=None, services=(), off=False):
    with conn() as c:
        c.execute("INSERT INTO slots (user_id, weekday, date, venue, start_hour, end_hour, services, off) "
                  "VALUES (?,?,?,?,?,?,?,?)",
                  (uid, weekday, date, (venue or "").strip(), start_hour, end_hour, json.dumps(list(services)), int(off)))


def slots(uid):
    with conn() as c:
        rows = c.execute("SELECT * FROM slots WHERE user_id=? ORDER BY off, date, weekday, start_hour", (uid,)).fetchall()
    return [dict(r, services=json.loads(r["services"] or "[]")) for r in rows]


def delete_slot(uid, sid):
    with conn() as c:
        c.execute("DELETE FROM slots WHERE id=? AND user_id=?", (sid, uid))


def upcoming(uid, days: int = 14):
    """Concrete sittings for the next `days` days: weekly slots + one-off extras, minus days off."""
    today = pd.Timestamp.today().normalize()
    all_slots = slots(uid)
    offs = {s["date"] for s in all_slots if s["off"] and s["date"]}
    out = []
    for d in range(days):
        date = today + pd.Timedelta(days=d)
        ds = str(date.date())
        if ds in offs:
            continue
        for s in all_slots:
            if s["off"]:
                continue
            if (s["weekday"] is not None and s["weekday"] == date.weekday()) or s["date"] == ds:
                if d == 0 and s["end_hour"] <= pd.Timestamp.now().hour + pd.Timestamp.now().minute / 60:
                    continue  # already over today
                out.append({"key": f"{ds}|{s['id']}", "date": ds, "slot_id": s["id"], "venue": s["venue"],
                            "start": s["start_hour"], "end": s["end_hour"], "services": s["services"],
                            "day": date.strftime("%a"), "label": date.strftime("%a %d %b"),
                            "weekly": s["weekday"] is not None})
    out.sort(key=lambda o: (o["date"], o["start"]))
    return out


def reminders_due(uid):
    """Confirmed bookings for later today or tomorrow that haven't had a reminder yet."""
    now = pd.Timestamp.now()
    today, tomorrow = str(now.date()), str((now + pd.Timedelta(days=1)).date())
    with conn() as c:
        rows = c.execute("SELECT * FROM bookings WHERE user_id=? AND status='confirmed' AND COALESCE(reminded,0)=0 "
                         "AND slot_date IN (?,?) ORDER BY slot_date, slot_time", (uid, today, tomorrow)).fetchall()
    now_h = now.hour + now.minute / 60
    return [r for r in rows if r["slot_date"] == tomorrow or (r["slot_time"] or 0) > now_h]


def mark_reminded(uid, bid):
    with conn() as c:
        c.execute("UPDATE bookings SET reminded=1 WHERE id=? AND user_id=?", (bid, uid))


def public_profiles():
    """Every creator's public card data (the homepage lists them)."""
    with conn() as c:
        rows = c.execute("SELECT user_id, slug, data FROM profiles ORDER BY user_id DESC").fetchall()
    return [(r["user_id"], r["slug"], json.loads(r["data"] or "{}")) for r in rows]
