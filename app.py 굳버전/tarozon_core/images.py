from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .decks import Deck


def load_static_image_bytes(repo_root: Path, relative_path: str) -> bytes:
    p = repo_root / relative_path
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")
    img = Image.open(p)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return image_to_jpeg_bytes(img)


def load_card_image(deck: Deck, repo_root: Path, code: str) -> Image.Image:
    img_path = repo_root / deck.image_dir / f"{code}.jpg"
    if not img_path.exists():
        raise FileNotFoundError(f"Card image not found: {img_path}")
    img = Image.open(img_path)
    # Normalize to RGB for consistent JPEG output
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    return img


def image_to_jpeg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    # For RGBA, convert (JPEG doesn't support alpha)
    if img.mode == "RGBA":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def card_image_bytes(deck: Deck, repo_root: Path, code: str, reversed_: bool) -> bytes:
    img = load_card_image(deck=deck, repo_root=repo_root, code=code)
    if reversed_:
        img = img.rotate(180, expand=True)
    return image_to_jpeg_bytes(img)

