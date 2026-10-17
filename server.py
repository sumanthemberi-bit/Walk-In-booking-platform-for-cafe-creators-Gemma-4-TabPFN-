"""Walk-In platform: every side hustler gets their own app, page and data.

Run:  python -m uvicorn server:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
"""
import datetime as dt
import io
import json
import os
import re
import secrets
import threading
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import db
import llm
import media
import notify
import predict
import backup
import guard
import prompts
import samples
import vault
from markupsafe import Markup, escape

HERE = Path(__file__).parent

db.init()
try:  # dummy creators (tagged "Demo") are always there, so the marketplace never looks empty
    samples.add_samples()
except Exception as e:
    print("Could not add demo creators:", e)
if os.getenv("WALKIN_NO_BACKUP") != "1":
    backup.start_daily()
app = FastAPI(title="Walk-In")
app.add_middleware(SessionMiddleware, secret_key=vault.key(),
                   max_age=60 * 60 * 24 * 30, same_site="lax")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
media.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media.UPLOAD_DIR), name="media")
templates = Jinja2Templates(directory=HERE / "templates")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith(("/app", "/b/", "/reset")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


ERROR_TEXT = {404: ("Page not found", "That page doesn't exist, or the link is old."),
              403: ("Not allowed", "You don't have access to this page."),
              429: ("Slow down", "Too many tries. Please wait a few minutes and try again.")}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    title, text = ERROR_TEXT.get(exc.status_code, ("Something went wrong", "Please go back and try again."))
    if exc.status_code == 403 and exc.detail and exc.detail != "Forbidden":
        text = exc.detail
    return templates.TemplateResponse(request, "error.html", {"title": title, "text": text, "code": exc.status_code},
                                      status_code=exc.status_code)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    import traceback
    traceback.print_exception(exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Something went wrong on our side. Please try again."}, status_code=500)
    return templates.TemplateResponse(request, "error.html", {"title": "Something went wrong",
                                      "text": "That's our fault, not yours. Please go back and try again in a moment. "
                                              "Nothing you entered was shared.", "code": 500}, status_code=500)
def _nicedate(s):
    try:
        d = dt.datetime.fromisoformat(str(s))
    except ValueError:
        return s
    days = (dt.date.today() - d.date()).days
    return "today" if days == 0 else "yesterday" if days == 1 else d.strftime("%d %b")


templates.env.filters["nicedate"] = _nicedate
CATEGORIES = [c for c in db.CRAFTS if c != "Other"]
templates.env.globals.update(CATEGORIES=CATEGORIES, fmt_time=db.fmt_time, TIMES=db.TIMES, WEATHER=db.WEATHER, CRAFTS=db.CRAFTS,
                             LANGS=list(prompts.LANG_HINT), MESSAGE_TYPES=prompts.MESSAGE_TYPES)


# ---------------- helpers ----------------
class LoginRequired(Exception):
    pass


class GoElsewhere(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(LoginRequired)
async def _login_redirect(request, exc):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(GoElsewhere)
async def _go_elsewhere(request, exc):
    return RedirectResponse(exc.url, status_code=303)


def uid_of(request: Request) -> int:
    """The logged-in CREATOR. Customers who wander into /app are sent to their own page."""
    uid = request.session.get("uid")
    u = db.get_user(uid) if uid else None
    if not u:
        request.session.clear()
        raise LoginRequired()
    if u["role"] == "customer":
        raise GoElsewhere("/me")
    return uid


def customer_of(request: Request) -> int:
    uid = request.session.get("uid")
    u = db.get_user(uid) if uid else None
    if not u:
        request.session.clear()
        raise GoElsewhere("/login?as=customer")
    if u["role"] != "customer":
        raise GoElsewhere("/app")
    return uid


def viewer_of(request: Request):
    """Who is looking at a page: None, or {'role': 'customer'|'creator', 'name': ..., 'id': ...}."""
    uid = request.session.get("uid")
    u = db.get_user(uid) if uid else None
    if not u:
        return None
    if u["role"] == "customer":
        k = db.get_customer(uid)
        return {"role": "customer", "id": uid, "name": (k["name"] if k else "") or "You",
                "phone": k["phone"] if k else "", "email": k["email"] if k else ""}
    _, p = db.get_profile(uid)
    return {"role": "creator", "id": uid, "name": p.get("name") or u["username"]}


def public_base(request: Request) -> str:
    return os.getenv("PUBLIC_URL", str(request.base_url)).rstrip("/")


def contact_email() -> str:
    return db.get_platform("contact_email") or db.get_platform("sender_email")


templates.env.globals["contact_email"] = contact_email


def tip(text: str, label: str = "") -> Markup:
    """A small (?) help marker. Tap or hover to read what a feature does."""
    return Markup('<button type="button" class="tip" aria-label="Help: {l}" aria-expanded="false" data-tip="{t}">?</button>').format(
        l=label or text[:60], t=text)


templates.env.globals["tip"] = tip

# One customer-facing description per craft, for the homepage guide (no prices: each creator sets their own).
CRAFT_GUIDE = {
    "Tarot reading": "A card reading about a question on your mind: work, love, a decision. Usually 15 to 45 minutes at the table.",
    "Palm reading": "A reading of the lines on your hands. Quick and fun, often 10 to 20 minutes.",
    "Astrology": "A chat about your birth chart. Bring your birth date, time and place.",
    "Mehendi / henna": "Henna designs on your hands, from a small motif to a full hand. Give it time to dry before you leave.",
    "Caricature / portraits": "A sketch of you, drawn live. Takes 10 to 30 minutes, and you take the drawing home.",
    "Nail art": "Painted designs on your nails while you sip your coffee.",
    "Tattoo (temporary)": "Temporary tattoos that last a few days to two weeks. No needles.",
    "Face painting": "Painted designs for kids and adults, great for birthdays and events.",
    "Calligraphy": "Your name or a quote written beautifully, often on a card or bookmark you keep.",
    "Live music": "A song or short set, for a birthday, a proposal or just the table.",
}
CRAFT_PERSON = {"Tarot reading": "tarot reader", "Palm reading": "palm reader", "Astrology": "astrologer",
                "Mehendi / henna": "mehendi artist", "Caricature / portraits": "caricature artist", "Nail art": "nail artist",
                "Tattoo (temporary)": "temporary tattoo artist", "Face painting": "face painter",
                "Calligraphy": "calligrapher", "Live music": "musician"}
templates.env.globals["CRAFT_PERSON"] = CRAFT_PERSON
WHEN_FILTERS = {"today": "Today", "weekend": "This weekend", "week": "Next 7 days"}


PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*[\d,]+(?:\.\d+)?(?:\s*/-)?", re.I)
SEP_RE = re.compile(r"\s*(?:·|•|\||–|—| - |:)\s*")


def parse_service(line: str) -> dict:
    """'Soul Spread · 30 min · ₹3,333' or 'Love Reading – ₹399' -> name, detail, price."""
    line = line.strip().lstrip("-*• ").strip()
    prices = PRICE_RE.findall(line)
    price = prices[-1].strip() if prices else ""
    rest = PRICE_RE.sub("", line) if price else line
    parts = [x for x in SEP_RE.split(rest) if x.strip()]
    return {"name": parts[0].strip() if parts else line, "detail": " · ".join(x.strip() for x in parts[1:]),
            "price": price}


def services_list(p: dict) -> list[str]:
    return [parse_service(s)["name"] for s in p.get("services", "").splitlines() if s.strip()]


def insta_handle(s: str) -> str:
    """'@name', 'name' or 'https://www.instagram.com/name/' -> 'name'."""
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", s or "")
    return (m.group(1) if m else (s or "").strip().lstrip("@").split("/")[0].split("?")[0]).strip()


def render(request: Request, name: str, uid: int | None = None, **ctx):
    if uid:
        slug, profile = db.get_profile(uid)
        ctx.update(me=db.get_user(uid), slug=slug, profile=profile, counts=db.counts(uid), is_admin=db.is_admin(uid),
                   public_url=f"{public_base(request)}/u/{slug}")
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx.setdefault("viewer", viewer_of(request))
    ctx.setdefault("CITIES", known_cities())
    ctx.setdefault("city", current_city(request))
    return templates.TemplateResponse(request, name, ctx)


DEFAULT_CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi"]


def known_cities() -> list[str]:
    """Cities that have at least one listed creator, plus a few big ones people will look for."""
    found = {(p.get("city") or "").strip().title() for _, _, p in db.public_profiles()
             if p.get("name") and p.get("services") and (p.get("city") or "").strip()}
    return sorted(found | set(DEFAULT_CITIES), key=lambda c: (c != "Mumbai", c))


def current_city(request: Request) -> str:
    """The city picked in the header ('all' = every city). Remembered for the visit."""
    return request.session.get("city") or "Mumbai"


def flash(request: Request, text: str):
    request.session["flash"] = text


def go(url: str):
    return RedirectResponse(url, status_code=303)


# ---------------- auth ----------------
def areas_of(p: dict) -> list[str]:
    return [a.strip().title() for a in re.split(r"[,/\n]", p.get("areas", "")) if a.strip()][:6]


POSTER_TINTS = ["#5B3E8C", "#1F6F78", "#A0522D", "#2E5A88", "#7A2E5C", "#3E6B3A", "#8C5A1F", "#3B4A6B"]


def creator_card(uid: int, slug: str, p: dict) -> dict:
    prices = []
    for line in p.get("services", "").splitlines():
        pr = parse_service(line)["price"] if line.strip() else ""
        num = re.sub(r"[^\d]", "", pr)
        if num:
            prices.append(int(num))
    up = db.upcoming(uid, 7)
    now = dt.datetime.now()
    now_h, today = now.hour + now.minute / 60, str(now.date())
    nxt, live = None, False
    if up:
        o = up[0]
        live = o["date"] == today and o["start"] <= now_h < o["end"]
        if o["date"] == today:
            nxt = (f"Here now at {o['venue']}" if live else f"Today at {o['venue']}, {db.fmt_time(o['start'])}")
        else:
            nxt = f"{o['label']} at {o['venue']}"
    weekend_dates = {str(dt.date.today() + dt.timedelta(days=i)) for i in range(7)
                     if (dt.date.today() + dt.timedelta(days=i)).weekday() >= 5}
    return {"slug": slug, "name": p.get("name") or slug, "craft": p.get("craft", ""), "city": p.get("city", ""),
            "areas": areas_of(p), "venues": sorted({o["venue"] for o in up if o["venue"]}),
            "weekend": any(o["date"] in weekend_dates for o in up), "week": bool(up),
            "verified": bool(p.get("verified")), "sample": bool(p.get("sample")),
            "photo": p.get("photo"), "cover": p.get("cover"), "from": f"₹{min(prices):,}" if prices else "",
            "next": nxt, "today": bool(up and up[0]["date"] == today), "live": live,
            "tint": POSTER_TINTS[uid % len(POSTER_TINTS)], "initial": (p.get("name") or slug)[:1].upper()}


@app.get("/", response_class=HTMLResponse)
def landing(request: Request, q: str = "", c: str = "", a: str = "", w: str = "", city: str | None = None):
    if city is not None:
        city = city.strip()[:40]
        request.session["city"] = "all" if city.lower() in ("", "all") else city.title()
    cur = current_city(request)
    cards = [creator_card(uid, slug, p) for uid, slug, p in db.public_profiles()
             if p.get("name") and p.get("services")
             and (cur == "all" or (p.get("city") or "").strip().lower() == cur.lower())]
    cards.sort(key=lambda x: x["sample"])  # real creators always come first
    query = q.strip().lower()
    w = w if w in WHEN_FILTERS else ""
    shown = cards
    if c:
        shown = [x for x in shown if x["craft"] == c]
    if a:
        shown = [x for x in shown if a.lower() in [y.lower() for y in x["areas"]]
                 or any(a.lower() in v.lower() for v in x["venues"])]
    if w:
        shown = [x for x in shown if x[w]]
    if query:
        shown = [x for x in shown if query in " ".join([x["name"], x["craft"], x["next"] or "", x["city"],
                                                         *x["areas"], *x["venues"]]).lower()]
    all_areas = sorted({y for x in cards for y in x["areas"]})
    return render(request, "landing.html", cards=shown, total=len(cards), q=q, cat=c, area=a, when=w, city=cur,
                  areas=all_areas, WHEN_FILTERS=WHEN_FILTERS, guide=CRAFT_GUIDE, craft_counts={
                      k: sum(1 for x in cards if x["craft"] == k) for k in CRAFT_GUIDE},
                  today=[x for x in shown if x["today"]], featured=[x for x in cards if x["cover"]][:6],
                  signed_in=bool(request.session.get("uid")), filtering=bool(c or query or a or w))


@app.get("/creators", response_class=HTMLResponse)
def creators_page(request: Request):
    return render(request, "creators.html", signed_in=bool(request.session.get("uid")))


@app.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return render(request, "info.html", page="help", signed_in=bool(request.session.get("uid")))


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return render(request, "info.html", page="privacy", signed_in=bool(request.session.get("uid")))


@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return render(request, "info.html", page="terms", signed_in=bool(request.session.get("uid")))


@app.get("/join", response_class=HTMLResponse)
def join_page(request: Request):
    return render(request, "join.html")


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return render(request, "signup.html")


@app.get("/signup/creator")
def signup_creator_alias():
    return go("/signup")


@app.get("/creator/login")
def creator_login_alias():
    return go("/login?as=creator")


@app.get("/customer/login")
def customer_login_alias():
    return go("/login?as=customer")


@app.get("/signup/customer", response_class=HTMLResponse)
def customer_signup_page(request: Request, next: str = ""):
    return render(request, "signup_customer.html", next=safe_next(next))


def safe_next(url: str) -> str:
    """Only allow redirects back into this site (never to another website)."""
    return url if url.startswith("/") and not url.startswith("//") and "\\" not in url else ""


@app.post("/signup/customer")
def customer_signup(request: Request, name: str = Form(""), email: str = Form(""), phone: str = Form(""),
                    password: str = Form(""), agree: str = Form(""), next: str = Form("")):
    form = dict(name=name, email=email, phone=phone)
    if agree != "on":
        return render(request, "signup_customer.html", error="Please accept the terms to create your account.",
                      form=form, next=safe_next(next))
    try:
        uid = db.create_customer(name, email, phone, password)
    except ValueError as e:
        return render(request, "signup_customer.html", error=str(e), form=form, next=safe_next(next))
    last = request.session.get("last_booking") or {}
    request.session.clear()
    request.session["uid"] = uid
    linked = ""
    if last.get("token"):  # they just booked as a guest in this browser: keep that booking in their account
        b = db.booking_by_token(last["token"])
        if b and not b["customer_id"]:
            db.link_booking(b["id"], uid)
            linked = " Your booking is saved here too."
    flash(request, f"Welcome, {name.strip().split()[0]}! This is where your bookings live.{linked}")
    return go(safe_next(next) or "/me")


# ---------------- customer account ----------------
@app.get("/me", response_class=HTMLResponse)
def my_bookings(request: Request):
    uid = customer_of(request)
    rows = db.customer_bookings(uid)
    today = str(dt.date.today())
    upcoming = [b for b in rows if b["status"] in ("new", "confirmed") and (not b["slot_date"] or b["slot_date"] >= today)]
    past = [b for b in rows if b not in upcoming]
    return render(request, "me.html", customer=db.get_customer(uid), upcoming=upcoming, past=past)


@app.post("/me/details")
def my_details(request: Request, name: str = Form(...), phone: str = Form(...)):
    uid = customer_of(request)
    if not name.strip() or len(re.sub(r"\D", "", phone)) < 10:
        flash(request, "Please add your name and a WhatsApp number with at least 10 digits.")
    else:
        db.update_customer(uid, name, phone)
        flash(request, "Saved. New bookings will use these details.")
    return go("/me#account")


@app.post("/me/password")
def my_password(request: Request, current: str = Form(...), new: str = Form(...), again: str = Form(...)):
    uid = customer_of(request)
    if not db.check_login(db.get_user(uid)["username"], current):
        flash(request, "Your current password isn't right.")
    elif new != again:
        flash(request, "The two new passwords don't match.")
    else:
        try:
            db.set_password(uid, new)
            flash(request, "Password changed.")
        except ValueError as e:
            flash(request, str(e))
    return go("/me#account")


@app.post("/me/delete")
def my_delete(request: Request, confirm: str = Form("")):
    uid = customer_of(request)
    k = db.get_customer(uid)
    if confirm.strip().lower() != (k["email"] or "").lower():
        flash(request, "Type your email exactly to confirm.")
        return go("/me#account")
    db.delete_user(uid)  # bookings stay with the creators, unlinked from the account
    request.session.clear()
    return go("/")


@app.post("/signup")
def signup(request: Request, username: str = Form(...), password: str = Form(...),
           name: str = Form(""), craft: str = Form("Other"), city: str = Form(""), email: str = Form(""),
           agree: str = Form("")):
    form = dict(username=username, name=name, craft=craft, city=city, email=email)
    email = email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return render(request, "signup.html", error="That email doesn't look right.", form=form)
    if agree != "on":
        return render(request, "signup.html", error="Please accept the terms to create your page.", form=form)
    try:
        uid = db.create_user(username, password, name, craft, city, email)
    except ValueError as e:
        return render(request, "signup.html", error=str(e), form=form)
    request.session.clear()
    request.session["uid"] = uid
    flash(request, "Welcome! Your page is created. Follow the steps below to get it ready for customers.")
    return go("/app")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    who = request.query_params.get("as", "")
    return render(request, "login.html", next=safe_next(next), who=who if who in ("customer", "creator") else "")


LOGIN_FAILS: dict[str, list[float]] = defaultdict(list)
LOGIN_WINDOW, LOGIN_MAX_PER_USER, LOGIN_MAX_PER_IP = 15 * 60, 5, 20


def client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def _recent_fails(key: str) -> list[float]:
    cutoff = time.time() - LOGIN_WINDOW
    LOGIN_FAILS[key] = [t for t in LOGIN_FAILS[key] if t > cutoff]
    return LOGIN_FAILS[key]


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(""),
          who: str = Form("")):
    ukey, ikey = "u:" + username.strip().lower(), "ip:" + client_ip(request)
    if len(_recent_fails(ukey)) >= LOGIN_MAX_PER_USER or len(_recent_fails(ikey)) >= LOGIN_MAX_PER_IP:
        return render(request, "login.html", username=username, who=who, next=safe_next(next),
                      error="Too many wrong passwords. For your safety, please wait 15 minutes, "
                            "or reset your password.")
    uid = db.check_login(username, password)
    if not uid:
        LOGIN_FAILS[ukey].append(time.time())
        LOGIN_FAILS[ikey].append(time.time())
        left = LOGIN_MAX_PER_USER - len(LOGIN_FAILS[ukey])
        return render(request, "login.html", username=username, who=who, next=safe_next(next),
                      error="Wrong email, username or password."
                      + (f" {left} tr{'y' if left == 1 else 'ies'} left before a 15-minute pause." if left <= 2 else ""))
    LOGIN_FAILS.pop(ukey, None)
    request.session.clear()  # fresh session on every login
    request.session["uid"] = uid
    role = db.get_user(uid)["role"]
    return go(safe_next(next) or ("/me" if role == "customer" else "/app"))


# ---------------- password reset by email ----------------
def _reset_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(vault.key(), salt="walkin-password-reset")


def _pw_fingerprint(uid: int) -> str:
    """Changes whenever the password changes, so a reset link works only once."""
    with db.conn() as c:
        row = c.execute("SELECT pw_hash FROM users WHERE id=?", (uid,)).fetchone()
    return row["pw_hash"][-12:] if row else ""


@app.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request):
    return render(request, "forgot.html", platform_ready=bool(notify.platform_login()))


@app.post("/forgot")
def forgot(request: Request, username: str = Form(...)):
    ikey = "reset-ip:" + client_ip(request)
    if len(_recent_fails(ikey)) >= 5:
        raise HTTPException(429)
    LOGIN_FAILS[ikey].append(time.time())
    u = db.user_by_username(username)
    if u:
        if (u["role"] or "creator") == "customer":
            k = db.get_customer(u["id"])
            to = k["email"] if k else ""
        else:
            to = notify.owner_address(db.get_profile(u["id"])[1])
        if to and notify.platform_login():
            token = _reset_signer().dumps({"u": u["id"], "f": _pw_fingerprint(u["id"])})
            link = f"{public_base(request)}/reset/{token}"
            threading.Thread(target=lambda: notify._run([("reset", lambda: notify.password_reset(to, u["username"], link))]),
                             daemon=True).start()
    # Same answer either way, so nobody can use this page to find out which usernames exist.
    return render(request, "forgot.html", sent=True, platform_ready=bool(notify.platform_login()))


def _reset_uid(token: str):
    try:
        data = _reset_signer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("u")
    return uid if uid and db.get_user(uid) and data.get("f") == _pw_fingerprint(uid) else None


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(request: Request, token: str):
    return render(request, "reset.html", token=token, valid=bool(_reset_uid(token)))


@app.post("/reset/{token}")
def reset(request: Request, token: str, new: str = Form(...), again: str = Form(...)):
    uid = _reset_uid(token)
    if not uid:
        return render(request, "reset.html", token=token, valid=False)
    if new != again:
        return render(request, "reset.html", token=token, valid=True, error="The two passwords don't match.")
    try:
        db.set_password(uid, new)
    except ValueError as e:
        return render(request, "reset.html", token=token, valid=True, error=str(e))
    LOGIN_FAILS.pop("u:" + db.get_user(uid)["username"], None)  # a successful reset lifts the lockout
    request.session.clear()
    request.session["uid"] = uid
    flash(request, "Password changed. You're logged in.")
    return go("/app")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/")


# ---------------- home ----------------
@app.get("/app", response_class=HTMLResponse)
def home(request: Request):
    uid = uid_of(request)
    df = db.sittings_df(uid)
    week = df[pd.to_datetime(df["date"]) >= pd.Timestamp.today().normalize() - pd.Timedelta(days=6)] if len(df) else df
    stats = {
        "sittings": len(df), "customers": int(df["customers"].sum()) if len(df) else 0,
        "earned": int(df["earnings"].sum()) if len(df) else 0,
        "week_earned": int(week["earnings"].sum()) if len(week) else 0,
        "week_customers": int(week["customers"].sum()) if len(week) else 0,
    }
    _, p = db.get_profile(uid)
    has_slots = bool(db.slots(uid))
    steps = [
        ("Add a photo of yourself or your work", "Pages with a photo get far more trust.", bool(p.get("photo") or p.get("cover")), "/app/profile#photos"),
        ("Add your services and prices", "One per line, like: Love reading · 20 min · ₹500", bool(p.get("services")), "/app/profile"),
        ("Set when and where you sit", "Customers can only book times you add here.", has_slots, "/app/schedule"),
        ("Add your WhatsApp number", "So customers can message you, and you can confirm them in one tap.", bool(p.get("whatsapp")), "/app/profile"),
        ("Add your area", "So people searching 'Bandra' or 'Juhu' find you.", bool(p.get("areas")), "/app/profile"),
        ("Teach the AI your voice", "Paste 3 or 4 messages you've sent to clients.", bool(p.get("voice_samples")), "/app/profile#voice"),
    ]
    todo = []
    if len(df) < predict.MIN_ROWS:
        todo.append((f"Log {predict.MIN_ROWS - len(df)} more sittings to unlock Best slots", "/app/log"))
    ok, status = llm.is_running()
    up = db.upcoming(uid, 7)
    today = [x for x in up if x["date"] == str(dt.date.today())]
    with db.conn() as c:
        todays_bookings = c.execute("SELECT * FROM bookings WHERE user_id=? AND slot_date=? AND status IN ('new','confirmed') "
                                    "ORDER BY slot_time", (uid, str(dt.date.today()))).fetchall()
    reminders = []
    for b in db.reminders_due(uid):
        day = "today" if b["slot_date"] == str(dt.date.today()) else "tomorrow"
        when = f"{day} at {db.fmt_time(b['slot_time'])}" + (f" at {b['venue']}" if b["venue"] else "")
        first = (b["name"] or "").split()[0] if b["name"] else ""
        text = (f"Hi {first}, a quick reminder of your {b['service'] or 'session'} with "
                f"{p.get('name') or 'me'} {when}. See you there! If you need to change anything, just reply here.")
        digits = "".join(ch for ch in b["phone"] or "" if ch.isdigit())
        reminders.append({"id": b["id"], "name": b["name"], "when": when, "service": b["service"],
                          "wa": f"https://wa.me/{digits}?text={quote(text)}" if digits else ""})
    return render(request, "home.html", uid, stats=stats, todo=todo, steps=steps,
                  steps_done=sum(1 for s in steps if s[2]), ai_ok=ok, ai_status=status, reminders=reminders,
                  recent=[b for b in db.bookings(uid) if b["status"] == "new"][:4], today=today,
                  next_up=up[0] if up and not today else None, todays_bookings=todays_bookings)


# ---------------- profile ----------------
@app.get("/app/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    return render(request, "profile.html", uid, gmail_connected=bool(notify.gmail_login(p)),
                  email_ready=notify.email_ready(p), platform_ready=bool(notify.platform_login()),
                  callmebot_number=notify.CALLMEBOT_NUMBER, callmebot_text=notify.CALLMEBOT_TEXT)


@app.post("/app/profile")
async def profile_save(request: Request):
    uid = uid_of(request)
    form = await request.form()
    _, p = db.get_profile(uid)
    for k in ("name", "craft", "city", "areas", "whatsapp", "instagram", "upi", "services", "schedule", "bio",
              "bio_notes", "voice_samples"):
        if k in form:
            p[k] = str(form[k]).strip()
    p["booking_open"] = form.get("booking_open") == "on"
    p["instagram"] = insta_handle(p.get("instagram", ""))
    problems = []
    for kind in ("photo", "cover"):
        up = form.get(kind)
        if form.get(f"remove_{kind}") == "on":
            media.delete(p.get(kind, ""))
            p[kind] = ""
        elif up is not None and getattr(up, "filename", ""):
            try:
                name = await run_in_threadpool(media.save_image, uid, kind, await up.read())
                media.delete(p.get(kind, ""))
                p[kind] = name
            except ValueError as e:
                problems.append(str(e))
    try:
        db.save_profile(uid, p, slug=str(form.get("slug", "")).strip() or None)
        flash(request, " ".join(problems) if problems else "Saved. Your public page is updated.")
    except ValueError as e:
        flash(request, str(e))
    return go("/app/profile")


@app.post("/app/alerts/{action}")
async def alerts(request: Request, action: str):
    uid = uid_of(request)
    slug, p = db.get_profile(uid)
    if action == "save":
        form = await request.form()
        if "callmebot_key" in form:
            p["callmebot_key"] = str(form["callmebot_key"]).strip()[:40]
        if "alert_email" in form:
            p["alert_email"] = str(form["alert_email"]).strip()[:120]
        if "email_alerts" in form:
            p["email_alerts"] = form["email_alerts"] == "on"
        if form.get("whatsapp"):
            p["whatsapp"] = str(form["whatsapp"]).strip()
        db.save_profile(uid, p)
        flash(request, "Saved. Press “Send test alert” to check.")
    elif action == "ntfy-on":
        p["ntfy_topic"] = notify.new_topic(slug)
        db.save_profile(uid, p)
        flash(request, "Push alerts on. Subscribe to your topic in the ntfy app.")
    elif action == "ntfy-off":
        p["ntfy_topic"] = ""
        db.save_profile(uid, p)
        flash(request, "Push alerts off.")
    elif action == "test":
        res = await run_in_threadpool(notify.test_alert, p, f"{public_base(request)}/app/bookings")
        flash(request, ("Test alert: " + " · ".join(f"{k} {v}" for k, v in res.items())) if res
              else "Turn on at least one alert channel first.")
    return go("/app/profile#alerts")


@app.post("/app/email/connect")
async def email_connect(request: Request, address: str = Form(...), app_password: str = Form(...)):
    uid = uid_of(request)
    address, app_password = address.strip(), app_password.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address):
        flash(request, "That doesn't look like an email address.")
        return go("/app/profile#email")
    try:
        await run_in_threadpool(notify.check_gmail, address, app_password)
    except Exception as e:
        hint = (" Use the 16-letter app password, not your normal Gmail password."
                if "535" in str(e) or "Username and Password" in str(e) else "")
        flash(request, f"Gmail didn't accept that login.{hint} ({str(e)[:120]})")
        return go("/app/profile#email")
    _, p = db.get_profile(uid)
    p.update(smtp_email=address, smtp_pass=vault.seal(app_password), email_alerts=True)
    db.save_profile(uid, p)
    flash(request, f"Gmail connected: {address}. Booking alerts will arrive there, and you can reply to customers by email.")
    return go("/app/profile#email")


@app.post("/app/email/disconnect")
def email_disconnect(request: Request):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    p.update(smtp_email="", smtp_pass="")
    db.save_profile(uid, p)
    flash(request, "Gmail disconnected and the stored password deleted.")
    return go("/app/profile#email")


@app.get("/app/qr.png")
def my_qr(request: Request):
    import qrcode

    uid = uid_of(request)
    slug, _ = db.get_profile(uid)
    img = qrcode.make(f"{public_base(request)}/u/{slug}", box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{slug}-qr.png"'})


# ---------------- sittings log ----------------
@app.get("/app/log", response_class=HTMLResponse)
def log_page(request: Request):
    uid = uid_of(request)
    df = db.sittings_df(uid)
    venues = sorted(df["cafe"].unique().tolist()) if len(df) else []
    return render(request, "log.html", uid, rows=df.to_dict("records"), venues=venues, today=dt.date.today())


@app.post("/app/log")
def log_add(request: Request, date: str = Form(...), start: float = Form(...), end: float = Form(...),
            venue: str = Form(""), new_venue: str = Form(""), weather: str = Form("Clear"),
            customers: int = Form(0), earnings: float = Form(0)):
    uid = uid_of(request)
    cafe = (new_venue or venue).strip()
    if not cafe:
        flash(request, "Pick or type a venue.")
    elif end <= start:
        flash(request, "End time must be after start time.")
    else:
        db.add_sitting(uid, date, start, end, cafe, weather, customers, earnings)
        predict_cache.pop(uid, None)
        flash(request, f"Saved: {cafe}, {customers} customers, ₹{int(earnings):,}.")
    return go("/app/log")


@app.post("/app/log/{sid}/delete")
def log_delete(request: Request, sid: int):
    uid = uid_of(request)
    db.delete_sitting(uid, sid)
    predict_cache.pop(uid, None)
    return go("/app/log")


@app.post("/app/log/import")
async def log_import(request: Request, file: UploadFile = File(...), replace: str = Form("")):
    uid = uid_of(request)
    try:
        df = pd.read_csv(io.BytesIO(await file.read()))
        if replace == "on":
            db.clear_sittings(uid)
        n = db.import_sittings_csv(uid, df)
        flash(request, f"Imported {n} sittings.")
    except Exception as e:  # bad CSV: tell the user, don't crash
        flash(request, f"Couldn't import that file: {e}")
    predict_cache.pop(uid, None)
    return go("/app/log")


@app.post("/app/log/clear")
def log_clear(request: Request):
    uid = uid_of(request)
    db.clear_sittings(uid)
    predict_cache.pop(uid, None)
    flash(request, "All sittings deleted.")
    return go("/app/log")


# ---------------- messages ----------------
@app.get("/app/messages", response_class=HTMLResponse)
def messages_page(request: Request, booking: int | None = None):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    pre = {}
    if booking:
        b = db.get_booking(uid, booking)
        if b:
            pre = {"kind": "Booking confirmation", "client": b["name"], "phone": b["phone"],
                   "service": b["service"], "booking": b["preferred"]}
    nxt = next(iter(db.upcoming(uid, 14)), None)
    next_spot = f"{nxt['label']}, {db.fmt_time(nxt['start'])}–{db.fmt_time(nxt['end'])} at {nxt['venue']}" if nxt else ""
    return render(request, "messages.html", uid, services=services_list(p), history=db.messages(uid, 20), pre=pre,
                  next_spot=next_spot)


@app.post("/api/message")
def api_message(request: Request, kind: str = Form(...), language: str = Form("English"),
                client: str = Form(...), phone: str = Form(""), service: str = Form(""),
                notes: str = Form(""), next_spot: str = Form(""), booking: str = Form("")):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    if kind not in prompts.MESSAGE_TYPES or language not in prompts.LANG_HINT:
        raise HTTPException(400, "Unknown message type or language.")
    user = (f"Client: {client}\nService: {service or 'not specified'}\n"
            + (f"Session notes: {notes}\n" if notes else "")
            + (f"Next available: {next_spot}\n" if next_spot else "")
            + (f"Session: {booking}\n" if booking else "")
            + (f"UPI ID: {p['upi']}\n" if kind == "Payment reminder" and p.get("upi") else ""))
    try:
        text, problems, _ = guard.generate_checked(llm.generate, prompts.message_prompt(p, language, kind), user,
                                                   language, notes)
    except Exception as e:
        return JSONResponse({"error": f"The local AI didn't respond: {e}"}, status_code=503)
    mid = db.add_message(uid, client, phone, kind, service, notes or booking, text)
    digits = "".join(ch for ch in phone if ch.isdigit())
    return {"id": mid, "text": text, "wa": f"https://wa.me/{digits}" if digits else "",
            "warning": ("Please read this one carefully: " + " ".join(guard.RULE_TEXT[f] for f in problems)) if problems else ""}


@app.post("/api/message/{mid}/sent")
def api_message_sent(request: Request, mid: int, text: str = Form(...)):
    uid = uid_of(request)
    db.mark_sent(uid, mid, text)
    return {"ok": True}


# ---------------- posts ----------------
@app.get("/app/post", response_class=HTMLResponse)
def post_page(request: Request):
    uid = uid_of(request)
    return render(request, "post.html", uid)


@app.post("/api/post")
def api_post(request: Request, idea: str = Form(...), platform: str = Form("Instagram"),
             language: str = Form("English")):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    if language not in prompts.LANG_HINT:
        raise HTTPException(400, "Unknown language.")
    user = f"Post idea: {idea}\nServices:\n{p.get('services', '')}\nSchedule:\n{p.get('schedule', '')}"
    try:
        return {"text": llm.generate(prompts.social_prompt(p, language, platform[:30]), user, 0.9)}
    except Exception as e:
        return JSONResponse({"error": f"The local AI didn't respond: {e}"}, status_code=503)


@app.post("/api/bio")
def api_bio(request: Request, notes: str = Form(...)):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    try:
        return {"text": llm.generate(prompts.bio_prompt(), f"{p.get('craft')} in {p.get('city')}. Notes: {notes}")}
    except Exception as e:
        return JSONResponse({"error": f"The local AI didn't respond: {e}"}, status_code=503)


# ---------------- best slots ----------------
predict_cache: dict[int, tuple] = {}


@app.get("/app/slots", response_class=HTMLResponse)
def slots_page(request: Request, run: int = 0):
    uid = uid_of(request)
    df = db.sittings_df(uid)
    ctx = {"n": len(df), "min_rows": predict.MIN_ROWS}
    if len(df) >= predict.MIN_ROWS:
        ctx["summary"] = predict.cafe_summary(df).to_dict("records")
        key = (len(df), int(df["earnings"].sum()), str(dt.date.today()))
        cached = predict_cache.get(uid)
        if cached and cached[0] == key:
            ranked, method = cached[1]
        elif run:
            ranked, method = predict.best_slots(df)
            predict_cache[uid] = (key, (ranked, method))
        else:
            ranked = method = None
        if ranked is not None:
            best = ranked.groupby("date", sort=False).first().reset_index().sort_values("date")
            ctx.update(method=method, best=best.to_dict("records"), top=ranked.head(15).to_dict("records"),
                       max_e=max(int(best["pred_earnings"].max()), 1))
    return render(request, "slots.html", uid, **ctx)


# ---------------- bookings ----------------
@app.get("/app/bookings", response_class=HTMLResponse)
def bookings_page(request: Request):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    rows = db.bookings(uid)
    seen: dict[str, int] = {}
    for b in rows:
        key = re.sub(r"\D", "", b["phone"] or "")[-10:]
        if key:
            seen[key] = seen.get(key, 0) + 1
    return render(request, "bookings.html", uid, rows=rows, gmail=notify.email_ready(p),
                  repeat_customers=sum(1 for n in seen.values() if n > 1))


@app.post("/api/bookings/{bid}/draft-email")
def api_draft_email(request: Request, bid: int, language: str = Form("English")):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    b = db.get_booking(uid, bid)
    if not b:
        raise HTTPException(404)
    kind = "Booking confirmation"
    user = (f"Client: {b['name']}\nService: {b['service'] or 'not specified'}\n"
            f"Session: {b['preferred'] or 'time to be agreed'}\n"
            + (f"Client's note: {b['note']}\n" if b["note"] else "")
            + "This is an email, so it can be 3-6 sentences and should end with a sign-off with your name.")
    try:
        text = llm.generate(prompts.message_prompt(p, language if language in prompts.LANG_HINT else "English", kind), user)
    except Exception as e:
        return JSONResponse({"error": f"The local AI didn't respond: {e}"}, status_code=503)
    return {"subject": f"Your {b['service'] or 'session'} booking with {p.get('name') or 'me'}", "text": text}


@app.post("/app/bookings/{bid}/email")
async def booking_email(request: Request, bid: int, subject: str = Form(...), text: str = Form(...)):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    b = db.get_booking(uid, bid)
    if not b or not b["email"]:
        raise HTTPException(404)
    try:
        await run_in_threadpool(notify.send_email, p, b["email"], subject.strip()[:150], text.strip()[:5000],
                                p.get("smtp_email", ""))
    except Exception as e:
        flash(request, f"Email not sent: {str(e)[:160]}")
        return go("/app/bookings")
    db.add_message(uid, b["name"], b["email"], "Email reply", b["service"], subject, text)
    with db.conn() as c:
        c.execute("UPDATE messages SET sent=1 WHERE id=(SELECT MAX(id) FROM messages WHERE user_id=?)", (uid,))
    if b["status"] == "new":
        db.set_booking_status(uid, bid, "confirmed")
    flash(request, f"Email sent to {b['name']} ({b['email']}).")
    return go("/app/bookings")


@app.post("/app/bookings/{bid}/reminded")
def booking_reminded(request: Request, bid: int):
    uid = uid_of(request)
    db.mark_reminded(uid, bid)
    if request.headers.get("accept", "").startswith("application/json"):
        return {"ok": True}
    return go("/app")


@app.post("/app/bookings/{bid}/{status}")
async def booking_status(request: Request, bid: int, status: str):
    uid = uid_of(request)
    if status not in ("confirmed", "done", "declined", "new"):
        raise HTTPException(400)
    b = db.get_booking(uid, bid)
    if not b:
        raise HTTPException(404)
    db.set_booking_status(uid, bid, status)
    if status == "confirmed":
        slug, p = db.get_profile(uid)
        info = {**dict(b), "status_url": f"{public_base(request)}/b/{db.ensure_token(uid, bid)}"}
        res = await run_in_threadpool(notify.booking_confirmed, p, info, f"{public_base(request)}/u/{slug}")
        if res == "sent":
            flash(request, f"Confirmed. {b['name']} got a confirmation email at {b['email']}. Send a WhatsApp too:")
        elif res:
            flash(request, f"Confirmed, but the email to {b['name']} failed ({res}). Send them a WhatsApp:")
        else:
            flash(request, f"Confirmed. {b['name']} didn't leave an email, so send them this WhatsApp:")
        return go(f"/app/messages?booking={bid}")
    return go("/app/bookings")


# ---------------- schedule ----------------
@app.get("/app/schedule", response_class=HTMLResponse)
def schedule_page(request: Request):
    uid = uid_of(request)
    _, p = db.get_profile(uid)
    all_slots = db.slots(uid)
    venues = sorted({s["venue"] for s in all_slots if s["venue"]} |
                    set(db.sittings_df(uid)["cafe"].unique().tolist()))
    weekly = [s for s in all_slots if s["weekday"] is not None and not s["off"]]
    extras = [s for s in all_slots if s["date"] and not s["off"] and s["date"] >= str(dt.date.today())]
    offs = [s for s in all_slots if s["off"] and s["date"] >= str(dt.date.today())]
    return render(request, "schedule.html", uid, weekly=weekly, extras=extras, offs=offs, venues=venues,
                  services=services_list(p), upcoming=sittings_with_times(uid, 14),
                  DAYNAMES=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])


@app.post("/app/schedule/add")
async def schedule_add(request: Request):
    uid = uid_of(request)
    form = await request.form()
    venue = (str(form.get("new_venue", "")).strip() or str(form.get("venue", "")).strip())[:80]
    try:
        start, end = float(form.get("start", 0)), float(form.get("end", 0))
    except ValueError:
        start = end = 0
    services = [str(x) for x in form.getlist("services")]
    days = [int(d) for d in form.getlist("days") if str(d).isdigit() and 0 <= int(d) <= 6]
    date = str(form.get("date", "")).strip()
    if not venue:
        flash(request, "Add the place (cafe name, or 'Online').")
    elif end <= start:
        flash(request, "End time must be after start time.")
    elif form.get("kind") == "once":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            flash(request, "Pick a date.")
        else:
            db.add_slot(uid, venue, start, end, date=date, services=services)
            flash(request, f"Added {venue} on {date}, {db.fmt_time(start)}–{db.fmt_time(end)}.")
    elif not days:
        flash(request, "Tick at least one day.")
    else:
        for d in days:
            db.add_slot(uid, venue, start, end, weekday=d, services=services)
        names = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in days)
        flash(request, f"Every {names}: {venue}, {db.fmt_time(start)}–{db.fmt_time(end)}. Your page is updated.")
    return go("/app/schedule")


@app.post("/app/schedule/off")
def schedule_off(request: Request, date: str = Form(...)):
    uid = uid_of(request)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        db.add_slot(uid, "", 0, 0, date=date, off=True)
        flash(request, f"Marked {date} as a day off. Customers can't book it.")
    return go("/app/schedule")


@app.post("/app/schedule/{sid}/delete")
def schedule_delete(request: Request, sid: int):
    uid = uid_of(request)
    db.delete_slot(uid, sid)
    return go("/app/schedule")


# ---------------- platform admin ----------------
def admin_of(request: Request) -> int:
    uid = uid_of(request)
    if not db.is_admin(uid):
        raise HTTPException(403, "Only the platform admin can open this page.")
    return uid


@app.get("/app/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    uid = admin_of(request)
    with db.conn() as c:
        stats = {"users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                 "bookings": c.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]}
    return render(request, "admin.html", uid, sender=db.get_platform("sender_email"), contact=db.get_platform("contact_email"), n_samples=len(samples.sample_ids()),
                  demo_logins=samples.demo_logins(), demo_password=samples.DEMO_PASSWORD,
                  connected=bool(notify.platform_login()), stats=stats, users=db.list_users(),
                  backups=backup.list_backups()[:5])


@app.post("/app/admin/reset/{target}")
def admin_reset(request: Request, target: int):
    admin_of(request)
    u = db.get_user(target)
    if not u:
        raise HTTPException(404)
    temp = f"walk-{secrets.randbelow(9000) + 1000}-{secrets.token_hex(2)}"
    db.set_password(target, temp)
    flash(request, f"New password for {u['username']}: {temp}  (share it with them privately; they can change it in My data)")
    return go("/app/admin#users")


@app.post("/app/admin/verify/{target}")
def admin_verify(request: Request, target: int):
    admin_of(request)
    if not db.get_user(target):
        raise HTTPException(404)
    slug, p = db.get_profile(target)
    p["verified"] = not p.get("verified")
    db.save_profile(target, p)
    flash(request, f"{p.get('name') or slug} is {'now marked verified' if p['verified'] else 'no longer verified'}.")
    return go("/app/admin#users")


@app.post("/app/admin/{action}")
async def admin_action(request: Request, action: str):
    uid = admin_of(request)
    form = await request.form()
    if action == "connect":
        address, pw = str(form.get("address", "")).strip(), str(form.get("app_password", "")).strip()
        try:
            await run_in_threadpool(notify.check_gmail, address, pw)
        except Exception as e:
            flash(request, f"Gmail didn't accept that login. Use the 16-letter app password. ({str(e)[:120]})")
            return go("/app/admin")
        db.set_platform("sender_email", address)
        db.set_platform("sender_pass", vault.seal(pw))
        flash(request, f"Platform email connected: {address}. Booking emails are now on for every user.")
    elif action == "disconnect":
        db.set_platform("sender_email", "")
        db.set_platform("sender_pass", "")
        flash(request, "Platform email disconnected.")
    elif action == "contact":
        addr = str(form.get("contact", "")).strip()[:120]
        if addr and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr):
            flash(request, "That doesn't look like an email address.")
        else:
            db.set_platform("contact_email", addr)
            flash(request, "Contact email saved. It's shown on the Help, Privacy and Terms pages.")
    elif action == "samples-add":
        n = await run_in_threadpool(samples.add_samples)
        flash(request, f"Added {n} missing demo creators." if n else "All demo creators are there. Their passwords are reset.")
    elif action == "samples-remove":
        n = await run_in_threadpool(samples.remove_samples)
        flash(request, f"Removed {n} demo creators. They come back the next time Walk-In starts.")
    elif action == "backup":
        try:
            folder = await run_in_threadpool(backup.make_backup)
            flash(request, f"Backup saved: {folder.name}")
        except Exception as e:
            flash(request, f"Backup failed: {e}")
    elif action == "test":
        to = str(form.get("to", "")).strip()
        try:
            await run_in_threadpool(notify.platform_test, to)
            flash(request, f"Test email sent to {to}.")
        except Exception as e:
            flash(request, f"Test failed: {str(e)[:160]}")
    return go("/app/admin")


# ---------------- my data ----------------
@app.get("/app/data", response_class=HTMLResponse)
def data_page(request: Request):
    uid = uid_of(request)
    return render(request, "data.html", uid)


@app.post("/app/password")
def change_password(request: Request, current: str = Form(...), new: str = Form(...), again: str = Form(...)):
    uid = uid_of(request)
    if not db.check_login(db.get_user(uid)["username"], current):
        flash(request, "Your current password isn't right.")
    elif new != again:
        flash(request, "The two new passwords don't match.")
    else:
        try:
            db.set_password(uid, new)
            flash(request, "Password changed.")
        except ValueError as e:
            flash(request, str(e))
    return go("/app/data#password")


@app.get("/app/export.zip")
def export(request: Request):
    uid = uid_of(request)
    slug, p = db.get_profile(uid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("profile.json", json.dumps({"page": slug, **{k: v for k, v in p.items() if k != "smtp_pass"}}, indent=2, ensure_ascii=False))
        for name, df in db.export_tables(uid).items():
            z.writestr(f"{name}.csv", df.drop(columns=["user_id"]).to_csv(index=False))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="walkin-{slug}.zip"'})


@app.post("/app/delete-account")
def delete_account(request: Request, confirm: str = Form("")):
    uid = uid_of(request)
    if confirm.strip().lower() != db.get_user(uid)["username"]:
        flash(request, "Type your username exactly to confirm.")
        return go("/app/data")
    _, p = db.get_profile(uid)
    for kind in ("photo", "cover"):
        media.delete(p.get(kind, ""))
    db.delete_user(uid)
    request.session.clear()
    return go("/")


def sittings_with_times(uid: int, days: int = 14):
    """Upcoming sittings, each with the start times still free (30-minute steps)."""
    out = []
    for o in db.upcoming(uid, days):
        taken = db.taken_times(uid, o["date"], o["venue"])
        now_h = dt.datetime.now().hour + dt.datetime.now().minute / 60
        times, t = [], o["start"]
        while t < o["end"]:
            if t not in taken and not (o["date"] == str(dt.date.today()) and t <= now_h):
                times.append(t)
            t += 0.5
        out.append({**o, "times": [{"v": t, "label": db.fmt_time(t)} for t in times],
                    "hours": f"{db.fmt_time(o['start'])} – {db.fmt_time(o['end'])}"})
    return out


# ---------------- public pages ----------------
@app.get("/u/{slug}", response_class=HTMLResponse)
def public_page(request: Request, slug: str, booked: str = ""):
    uid, p = db.profile_by_slug(slug)
    if not uid:
        raise HTTPException(404, "Page not found")
    booked = booked if re.fullmatch(r"[0-9a-f]{1,16}", booked or "") else ("1" if booked else "")
    digits = "".join(ch for ch in p.get("whatsapp", "") if ch.isdigit())
    wa = f"https://wa.me/{digits}?text={quote('Hi! I found your page and would like to book a session.')}" if digits else ""
    wa_booking = ""
    last = request.session.get("last_booking")
    if booked and digits and last and last.get("slug") == slug:
        b = db.booking_by_token(last.get("token", ""))
        first = p.get("name", "").split()[0] if p.get("name") else ""
        text = (f"Hi {first}, this is {last['name']}. I've just booked "
                f"{last['service'] or 'a session'} for {last['preferred'] or 'a time we can agree on'} on Walk-In"
                + (f" (booking #{b['id']})" if b else "") + ". Looking forward to it!")
        wa_booking = f"https://wa.me/{digits}?text={quote(text)}"
    sittings = sittings_with_times(uid)
    now = dt.datetime.now()
    now_h, today = now.hour + now.minute / 60, str(now.date())
    status = None
    here = next((o for o in sittings if o["date"] == today and o["start"] <= now_h < o["end"]), None)
    later = next((o for o in sittings if o["date"] == today and o["start"] > now_h), None)
    if here:
        status = {"live": True, "text": f"Here now at {here['venue']} until {db.fmt_time(here['end'])}"}
    elif later:
        status = {"live": False, "text": f"Today at {later['venue']} from {db.fmt_time(later['start'])}"}
    elif sittings:
        n = sittings[0]
        status = {"live": False, "text": f"Next: {n['label']} at {n['venue']}, {db.fmt_time(n['start'])}"}
    last_info = last if (booked and last and last.get("slug") == slug) else None
    card = creator_card(uid, slug, p)
    return render(request, "public.html", page=p, page_slug=slug, wa=wa, booked=booked, wa_booking=wa_booking,
                  tint=card["tint"], from_price=card["from"], langs="", signed_in=bool(request.session.get("uid")),
                  status=status, last=last_info, email_on=notify.email_ready(p),
                  owner_emailed=bool(notify.owner_address(p) and p.get("email_alerts", True) and notify.email_ready(p)),
                  sittings=sittings, sittings_json=[{"key": o["key"], "date": o["date"], "label": o["label"], "venue": o["venue"],
                                                     "times": o["times"], "services": o["services"]} for o in sittings],
                  services=[parse_service(s) for s in p.get("services", "").splitlines() if s.strip()],
                  schedule=[s for s in p.get("schedule", "").splitlines() if s.strip()],
                  service_names=services_list(p), insta=insta_handle(p.get("instagram", "")),
                  is_owner=request.session.get("uid") == uid)


BOOKING_LOCK = threading.Lock()  # check-then-insert must be atomic, or two people could grab one slot


@app.post("/u/{slug}/book")
def public_book(request: Request, slug: str, name: str = Form(...), phone: str = Form(...),
                service: str = Form(""), preferred: str = Form(""), note: str = Form(""), email: str = Form(""),
                when: str = Form(""), time: str = Form(""), website: str = Form("")):
    uid, p = db.profile_by_slug(slug)
    if not uid or not p.get("booking_open", True):
        raise HTTPException(404)
    if not website:  # honeypot field: bots fill it, people don't see it
        name, service, preferred = name.strip()[:80], service[:80], preferred.strip()[:120]
        email = email.strip()[:120] if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()) else ""
        if not name or not re.search(r"\d{6,}", re.sub(r"\D", "", phone)):
            flash(request, "Please add your name and a WhatsApp number so the booking can be confirmed.")
            return go(f"/u/{slug}#book")
        BOOKING_LOCK.acquire()
        try:
            slot_date = slot_time = venue = None
            if when and when != "other":  # a sitting from the owner's schedule: check it's real and still free
                occ = next((o for o in sittings_with_times(uid) if o["key"] == when), None)
                try:
                    t = float(time)
                except ValueError:
                    t = None
                if not occ or t not in [x["v"] for x in occ["times"]]:
                    flash(request, "Sorry, that time was just taken or isn't available. Please pick another.")
                    return go(f"/u/{slug}#book")
                if occ["services"] and service and service not in occ["services"]:
                    service = ""
                slot_date, slot_time, venue = occ["date"], t, occ["venue"]
                preferred = f"{occ['label']}, {db.fmt_time(t)} at {venue}"
            bid, token = db.add_booking(uid, name, phone[:30], service, preferred, note[:500], email, slot_date, slot_time, venue)
            v = viewer_of(request)
            if v and v["role"] == "customer":
                db.link_booking(bid, v["id"])
        finally:
            BOOKING_LOCK.release()
        status_url = f"{public_base(request)}/b/{token}"
        request.session["last_booking"] = {"slug": slug, "name": name, "service": service, "token": token,
                                           "linked": bool(v and v["role"] == "customer"), "email": email,
                                           "preferred": preferred, "note": note.strip()[:200]}
        notify.booking_alert(p, {"name": name, "phone": phone.strip()[:30] + (f" · {email}" if email else ""), "service": service,
                                 "preferred": preferred, "note": note.strip()[:200], "email": email,
                                 "status_url": status_url},
                             inbox_url=f"{public_base(request)}/app/bookings",
                             page_url=f"{public_base(request)}/u/{slug}")
    return go(f"/u/{slug}?booked={secrets.token_hex(4)}#book")


# ---------------- customer's own booking page ----------------
@app.get("/b/{token}", response_class=HTMLResponse)
def booking_status_page(request: Request, token: str):
    b = db.booking_by_token(token)
    if not b:
        raise HTTPException(404)
    slug, p = db.get_profile(b["user_id"])
    digits = "".join(ch for ch in p.get("whatsapp", "") if ch.isdigit())
    past = bool(b["slot_date"] and b["slot_date"] < str(dt.date.today()))
    return render(request, "booking_status.html", b=b, page=p, page_slug=slug, past=past,
                  wa=(f"https://wa.me/{digits}?text=" + quote("Hi! About my booking: " + (b["preferred"] or ""))) if digits else "",
                  can_cancel=b["status"] in ("new", "confirmed") and not past)


@app.post("/b/{token}/cancel")
def booking_cancel(request: Request, token: str):
    b = db.booking_by_token(token)
    if not b:
        raise HTTPException(404)
    if b["status"] in ("new", "confirmed"):
        db.set_booking_status(b["user_id"], b["id"], "cancelled")
        _, p = db.get_profile(b["user_id"])
        notify.booking_cancelled(p, dict(b), f"{public_base(request)}/app/bookings")
        flash(request, "Your booking is cancelled, and the creator has been told. Thanks for letting them know.")
    return go(f"/b/{token}")


# ---------------- installable app ----------------
@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Walk-In", "short_name": "Walk-In", "id": "/app", "start_url": "/app", "scope": "/",
        "display": "standalone", "background_color": "#FFFFFF", "theme_color": "#2B2D3A",
        "description": "Bookings, schedule and messages for your cafe sittings.",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }, media_type="application/manifest+json")


SW = r"""// Walk-In service worker (v2): installable app + read-only offline mode.
// Online: every page comes fresh from the server, and a copy is saved on this phone.
// Offline: the saved copy is shown with a banner, and all buttons and forms are switched off.
const PAGES = 'walkin-pages-v2', FILES = 'walkin-files-v2';
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (![PAGES, FILES].includes(k)) await caches.delete(k);
  await self.clients.claim();
})()));

function page(title, text) {
  return new Response('<meta name=viewport content="width=device-width"><body style="font-family:system-ui;padding:32px;text-align:center">' +
    '<h2>' + title + '</h2><p>' + text + '</p><button onclick="history.length>1?history.back():location.reload()" style="padding:12px 20px;font-size:16px">Go back</button>',
    {headers: {'Content-Type': 'text/html'}});
}

const BANNER = savedAt => `<script>(function(){
  var t = new Date(${savedAt}), when = t.toLocaleString([], {weekday:'short', hour:'numeric', minute:'2-digit'});
  function bar(){
    var b = document.createElement('div');
    b.id = 'offline-bar';
    b.style.cssText = 'position:sticky;top:0;z-index:9999;background:#2B2D3A;color:#fff;font:500 14px system-ui;padding:10px 14px;text-align:center';
    b.textContent = 'Offline. Showing your saved copy from ' + when + '. Changes are off until you reconnect.';
    document.body.prepend(b);
    document.querySelectorAll('form').forEach(function(f){ f.addEventListener('submit', function(e){ e.preventDefault(); }, true); });
    document.querySelectorAll('form button, form input, form select, form textarea').forEach(function(x){ x.disabled = true; });
    window.addEventListener('online', function(){ b.textContent = 'Back online. Tap here to refresh.'; b.style.background = '#1EA83C'; b.onclick = function(){ location.reload(); }; });
  }
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', bar) : bar();
})();</script>`;

function key(url) { return url.origin + url.pathname; }

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (req.method !== 'GET') {
    // Signing in or out: forget the saved pages so the next person on this phone can't see them.
    if (url.pathname === '/logout' || url.pathname === '/login') e.waitUntil(caches.delete(PAGES));
    if (req.mode === 'navigate') e.respondWith(fetch(req).catch(() =>
      page('Not saved: you are offline', 'Your change did not reach Walk-In. Reconnect and try again.')));
    return;
  }

  // Photos, icons: show the saved one straight away and refresh it in the background.
  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/')) {
    e.respondWith(caches.open(FILES).then(async c => {
      const hit = await c.match(req);
      const net = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; });
      if (hit) { net.catch(() => {}); return hit; }
      return net;
    }));
    return;
  }

  if (req.mode !== 'navigate') return;
  const savable = !['/login', '/signup', '/forgot'].includes(url.pathname) && !url.pathname.startsWith('/b/')
    && !url.pathname.startsWith('/reset/') && !(url.pathname === '/' && url.search);
  e.respondWith((async () => {
    try {
      const r = await fetch(req);
      const html = (r.headers.get('content-type') || '').includes('text/html');
      if (savable && r.ok && !r.redirected && html) {
        const body = await r.clone().blob();
        const h = new Headers(r.headers); h.set('X-Saved-At', String(Date.now()));
        (await caches.open(PAGES)).put(key(url), new Response(body, {headers: h}));
      }
      return r;
    } catch (err) {
      const hit = await (await caches.open(PAGES)).match(key(url));
      if (!hit) return page('Walk-In is offline', 'This page has not been saved on your phone yet. Check your internet, or the Walk-In computer may be switched off.');
      const html = await hit.text();
      const tag = BANNER(hit.headers.get('X-Saved-At') || Date.now());
      const i = html.indexOf('</head>');
      return new Response(i < 0 ? tag + html : html.slice(0, i) + tag + html.slice(i), {headers: {'Content-Type': 'text/html'}});
    }
  })());
});
"""


@app.get("/sw.js")
def service_worker():
    return Response(SW, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/.well-known/assetlinks.json")
def assetlinks():
    """Proves to Android that the Walk-In app (from PWABuilder) and this site belong together,
    so the installed app opens full screen without the browser bar. Put PWABuilder's
    assetlinks.json in the Walk-In folder."""
    f = HERE / "assetlinks.json"
    if not f.exists():
        raise HTTPException(404, "assetlinks.json not set up")
    return Response(f.read_text(encoding="utf-8"), media_type="application/json")


@app.get("/healthz")
def health():
    return {"ok": True, "ai": llm.is_running()[0]}
