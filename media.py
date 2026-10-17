"""Profile and cover photos: checked, auto-rotated, cropped, resized and saved as JPEG."""
import io
import os
import secrets
from pathlib import Path

from PIL import Image, ImageOps

UPLOAD_DIR = Path(os.getenv("WALKIN_UPLOADS", Path(__file__).parent / "uploads"))
MAX_BYTES = 10 * 1024 * 1024
SIZES = {"photo": (600, 600), "cover": (1600, 900)}  # square poster / 16:9 banner


def save_image(uid: int, kind: str, data: bytes) -> str:
    """Returns the stored file name. Raises ValueError with a user-facing message."""
    if kind not in SIZES:
        raise ValueError("Unknown photo type.")
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("That photo is too large. Please use one under 10 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise ValueError("That file isn't a photo we can read. Use a JPG or PNG.")
    img = ImageOps.fit(img, SIZES[kind], method=Image.LANCZOS, centering=(0.5, 0.45))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uid}-{kind}-{secrets.token_hex(4)}.jpg"
    img.save(UPLOAD_DIR / name, "JPEG", quality=85, optimize=True, progressive=True)
    return name


def delete(name: str):
    if name and "/" not in name and "\\" not in name:
        try:
            (UPLOAD_DIR / name).unlink(missing_ok=True)
        except OSError:
            pass
