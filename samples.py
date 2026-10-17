"""Dummy creators, tagged "Demo" on every page, so the marketplace never looks empty.

Invented creators at invented cafes. They're added automatically when Walk-In starts.
Each has a login so the Creator studio side can be shown too; the admin sees the password on the Platform page.
"""
import re
import io
import math
import random
import secrets

from PIL import Image, ImageDraw, ImageFilter

import db
import media

def _demo_password() -> str:
    """Never stored in the code: set WALKIN_DEMO_PASSWORD, or it's derived from this install's private .secret_key.
    The admin sees it on the Platform page."""
    import hashlib, hmac, os
    import vault
    if os.getenv("WALKIN_DEMO_PASSWORD"):
        return os.environ["WALKIN_DEMO_PASSWORD"]
    return "demo-" + hmac.new(bytes.fromhex(vault.key()), b"walkin-demo-login", hashlib.sha256).hexdigest()[:10]


DEMO_PASSWORD = _demo_password()

# name, craft, areas, venue, [(weekdays, start, end)], services, bio, (colour1, colour2), pattern, has_cover
SAMPLES = [
    ("Moonlit Tarot", "Tarot reading", "Bandra", "Chai & Chapters, Bandra West", [((0, 1, 2, 3, 4, 5, 6), 17, 21)],
     "Quick question · 15 min · ₹300\nLove & relationships · 30 min · ₹600\nFull life spread · 45 min · ₹1,000",
     "I read tarot as a calm conversation, not a prediction. Bring one question, leave with a little more clarity.",
     ("#2A1B4D", "#7B4FA3"), "stars", True),
    ("Henna by Zoya", "Mehendi / henna", "Andheri", "The Window Seat, Andheri West", [((4, 5, 6), 15, 20)],
     "Single motif · 10 min · ₹200\nOne hand, one side · 30 min · ₹500\nBridal trial · 90 min · ₹2,500",
     "Arabic, Indo-western and minimal henna. Natural cone, no black henna, ever.",
     ("#7A3B12", "#C9772E"), "petals", True),
    ("Sketch Sahil", "Caricature / portraits", "Colaba", "Brew & Brush, Colaba", [((5, 6), 12, 18)],
     "Solo caricature · 15 min · ₹400\nCouple caricature · 25 min · ₹700\nPencil portrait (A4) · 40 min · ₹1,200",
     "Fast, funny caricatures and careful pencil portraits. You take the drawing home the same day.",
     ("#163B5C", "#3F86B8"), "lines", True),
    ("Lines & Fate", "Palm reading", "Juhu", "Sea Breeze Cafe, Juhu", [((2, 5, 6), 16, 20)],
     "Palm snapshot · 10 min · ₹250\nBoth hands, full reading · 25 min · ₹550",
     "Palm reading the old-fashioned way: your hands, a magnifying glass and an honest chat.",
     ("#3D2A1E", "#9C6B43"), "rings", False),
    ("Star Charts with Ira", "Astrology", "Powai", "Lakeside Beans, Powai", [((2, 5), 11, 16)],
     "Birth chart basics · 30 min · ₹800\nYear ahead · 45 min · ₹1,200",
     "Bring your birth date, time and place. We'll look at your chart together, in plain words.",
     ("#0F2D3A", "#2F7C8C"), "stars", False),
    ("Ink & Quill", "Calligraphy", "Bandra", "Chai & Chapters, Bandra West", [((6,), 11, 15)],
     "Name card · 10 min · ₹150\nQuote bookmark · 15 min · ₹250\nPlace cards, set of 10 · ₹900",
     "Your name or favourite line, hand-lettered on card you keep. Great as a small gift.",
     ("#1E2A22", "#4F7A5A"), "lines", False),
    ("Polish Pop", "Nail art", "Khar", "Corner Bakehouse, Khar West", [((4, 5), 13, 19)],
     "Accent nail art · 15 min · ₹300\nFull set art · 45 min · ₹900",
     "Playful nail art while you finish your coffee. Bring a picture, or let me surprise you.",
     ("#6B1F45", "#D2528E"), "dots", False),
    ("Strings at the Table", "Live music", "Lower Parel", "Mill Yard Cafe, Lower Parel", [((4, 5), 19, 22)],
     "Birthday song · 5 min · ₹500\nProposal set, 3 songs · 15 min · ₹2,000",
     "Acoustic guitar and vocals for birthdays, proposals, or just your table. Requests welcome.",
     ("#3A1D12", "#B0532B"), "rings", False),
    ("Temp Ink Studio", "Tattoo (temporary)", "Bandra", "Chai & Chapters, Bandra West", [((5, 6), 14, 19)],
     "Small temporary tattoo · 10 min · ₹300\nJagua (lasts ~2 weeks) · 25 min · ₹800",
     "Try a tattoo without the commitment. Skin-safe inks, designs from tiny to statement.",
     ("#1B1B1F", "#55555E"), "dots", False),
    ("Glitter Faces", "Face painting", "Thane", "Courtyard Cafe, Thane West", [((5, 6), 11, 17)],
     "Kids' face paint · 10 min · ₹250\nGlitter look (adults) · 15 min · ₹400",
     "Bright, skin-safe face paint for kids' birthdays and grown-ups who want sparkle.",
     ("#0E4D4A", "#2BB3A3"), "dots", False),
    ("Third Eye Tarot", "Tarot reading", "Koregaon Park", "Bean There, Koregaon Park", [((4, 5, 6), 16, 21)],
     "One card pull · 10 min · ₹250\nCareer spread · 30 min · ₹700\nYear ahead · 60 min · ₹1,500",
     "Gentle, practical tarot. No doom, no drama: just a clearer view of what's in front of you.",
     ("#24123A", "#8A3FB0"), "stars", True, "Pune"),
    ("Doodle Desk", "Caricature / portraits", "Indiranagar", "Filter Kaapi House, Indiranagar", [((5, 6), 12, 19)],
     "Quick doodle portrait · 10 min · ₹350\nFamily caricature · 30 min · ₹1,100",
     "Live caricatures in ink and watercolour. Kids, couples, whole families: everyone fits on the page.",
     ("#12334A", "#E07A3F"), "lines", False, "Bengaluru"),
]


def handle(name: str) -> str:
    return "demo_" + re.sub(r"[^a-z0-9]", "", name.lower())[:14]


def _art(size, c1, c2, pattern, seed):
    """A soft gradient with a simple pattern: decorative, clearly not a photo of a real person."""
    w, h = size
    rnd = random.Random(seed)
    img = Image.new("RGB", size, c1)
    top, bot = Image.new("RGB", size, c1), Image.new("RGB", size, c2)
    mask = Image.linear_gradient("L").rotate(rnd.choice([0, 45, 135, 180]), expand=False).resize(size)
    img = Image.composite(bot, top, mask)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = min(w, h)
    if pattern == "stars":
        for _ in range(int(w * h / 4000)):
            x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.choice([1, 1, 2, 3])
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randrange(90, 230)))
        cx, cy, R = w * 0.72, h * 0.32, s * 0.2
        d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=(255, 244, 214, 235))
        d.ellipse((cx - R + R * 0.45, cy - R - R * 0.1, cx + R + R * 0.45, cy + R - R * 0.1), fill=(0, 0, 0, 0))
    elif pattern == "petals":
        cx, cy = w * 0.62, h * 0.5
        for ring, n in ((s * 0.12, 8), (s * 0.24, 12), (s * 0.36, 16)):
            for i in range(n):
                a = 2 * math.pi * i / n
                x, y, r = cx + ring * math.cos(a), cy + ring * math.sin(a), s * 0.035
                d.ellipse((x - r, y - r * 1.6, x + r, y + r * 1.6), outline=(255, 236, 210, 200), width=max(2, s // 150))
        d.ellipse((cx - s * .05, cy - s * .05, cx + s * .05, cy + s * .05), fill=(255, 236, 210, 220))
    elif pattern == "lines":
        for i in range(14):
            y = h * (0.15 + 0.05 * i) + rnd.uniform(-8, 8)
            pts = [(x, y + 18 * math.sin(x / (s * 0.08) + i)) for x in range(0, w + 20, 20)]
            d.line(pts, fill=(255, 255, 255, 70 + 8 * i), width=max(2, s // 220))
    elif pattern == "rings":
        cx, cy = w * 0.65, h * 0.45
        for k in range(1, 9):
            R = s * 0.06 * k
            d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=(255, 230, 200, 200 - k * 18), width=max(2, s // 180))
    else:  # dots
        step = max(26, s // 14)
        for yy in range(0, h + step, step):
            for xx in range(0, w + step, step):
                r = rnd.choice([3, 4, 6, 8]) * s / 600
                d.ellipse((xx - r, yy - r, xx + r, yy + r), fill=(255, 255, 255, rnd.randrange(40, 160)))
    img = Image.alpha_composite(img.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(0.6))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def demo_logins() -> list[dict]:
    out = []
    for uid, slug, p in db.public_profiles():
        if p.get("sample"):
            u = db.get_user(uid)
            out.append({"name": p.get("name"), "username": u["username"] if u else "", "slug": slug,
                        "city": p.get("city", "")})
    return sorted(out, key=lambda x: (x["city"], x["name"]))


def sample_ids() -> list[int]:
    return [uid for uid, _, p in db.public_profiles() if p.get("sample")]


def add_samples() -> int:
    """Create any sample creators that don't exist yet. Returns how many were added."""
    have = {p.get("name"): uid for uid, _, p in db.public_profiles() if p.get("sample")}
    for uid in have.values():  # demo creators from an older version: give them the demo password too
        db.set_password(uid, DEMO_PASSWORD)
    added = 0
    for i, (name, craft, areas, venue, weekly, services, bio, cols, pattern, cover, *rest) in enumerate(SAMPLES):
        city = rest[0] if rest else "Mumbai"
        if name in have:
            continue
        username = handle(name)
        if db.user_by_username(username):
            username += secrets.token_hex(1)
        uid = db.create_user(username, DEMO_PASSWORD, name, craft, city)
        slug, p = db.get_profile(uid)
        p.update(sample=True, areas=areas, services=services, bio=bio, booking_open=True,
                 photo=media.save_image(uid, "photo", _art((600, 600), *cols, pattern, i)))
        if cover:
            p["cover"] = media.save_image(uid, "cover", _art((1600, 900), *cols, pattern, i + 100))
        db.save_profile(uid, p)
        for days, start, end in weekly:
            for d in days:
                db.add_slot(uid, venue, start, end, weekday=d)
        added += 1
    return added


def remove_samples() -> int:
    ids = sample_ids()
    for uid in ids:
        _, p = db.get_profile(uid)
        for kind in ("photo", "cover"):
            media.delete(p.get(kind, ""))
        db.delete_user(uid)
    return len(ids)


if __name__ == "__main__":  # python samples.py on | off
    import sys
    db.init()
    if len(sys.argv) > 1 and sys.argv[1] == "off":
        print(f"Demo mode off: removed {remove_samples()} demo creators.")
    else:
        n = add_samples()
        print(f"Demo mode on: {n} demo creators added ({len(sample_ids())} in total).")
        print(f"Log in as any of them with password: {DEMO_PASSWORD}")
        for d in demo_logins():
            print(f"  {d['username']:<24} {d['name']} ({d['city']})")
