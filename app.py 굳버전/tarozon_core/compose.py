from __future__ import annotations

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .decks import Deck
from .images import load_card_image
from .spreads import LayoutSpec, Spread


@dataclass(frozen=True)
class RenderResult:
    png_bytes: bytes
    width: int
    height: int


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    rgb = ImageColor.getrgb(color)
    if not isinstance(rgb, tuple) or len(rgb) < 3:
        return (255, 255, 255)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    # Keep aspect ratio; crop to fill (like CSS object-fit: cover)
    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(round(src_w * scale))
    new_h = int(round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _rotate_rgba(img: Image.Image, angle_deg: int) -> Image.Image:
    rgba = img.convert("RGBA")
    # expand=True to preserve full rotated bounds (esp 90/270)
    return rgba.rotate(angle_deg, expand=True, resample=Image.Resampling.BICUBIC)


@lru_cache(maxsize=512)
def _load_image_cached(path_str: str) -> Image.Image:
    p = Path(path_str)
    img = Image.open(p)
    # Keep original mode; downstream will convert as needed
    return img.copy()


@lru_cache(maxsize=2048)
def _render_layer_cached(path_str: str, target_w: int, target_h: int, angle: int) -> Image.Image:
    img = _load_image_cached(path_str)
    img = _resize_cover(img, target_w, target_h)
    img = _rotate_rgba(img, angle)
    return img


def compose_spread_image(
    *,
    repo_root: Path,
    deck: Deck,
    spread: Spread,
    codes_by_slot: dict[str, str],
    angles_by_slot: dict[str, int],
    render_back_for_missing: bool = True,
) -> RenderResult:
    """
    Compose a single PNG image of the spread using Pillow.

    - Uses spread.layout if present (absolute, with z-order overlap).
    - Rotation is per-slot and may be 0/180 (normal/reversed) or 90/270 (celtic cross overlay card), etc.
    """
    layout: LayoutSpec | None = spread.layout
    if layout is None:
        raise ValueError("Spread has no layout spec; cannot compose.")

    if layout.type != "absolute":
        raise ValueError(f"Unsupported layout type: {layout.type}")

    scale = float(layout.scale or 1.0)
    canvas_w = int(round(layout.canvas.width * scale))
    canvas_h = int(round(layout.canvas.height * scale))
    card_w = int(round(layout.card.width * scale))
    card_h = int(round(layout.card.height * scale))

    bg = _hex_to_rgb(layout.canvas.background)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*bg, 255))

    back_img: Image.Image | None = None
    if render_back_for_missing and deck.back_image:
        back_path = repo_root / deck.back_image
        if back_path.exists():
            back_img = _load_image_cached(str(back_path))

    # Render slots by z order
    render_slots = sorted(layout.slots, key=lambda s: (s.z, s.key))
    for s in render_slots:
        code = codes_by_slot.get(s.key)

        angle = int(angles_by_slot.get(s.key, 0))
        if code:
            img_path = repo_root / deck.image_dir / f"{code}.jpg"
            img = _render_layer_cached(str(img_path), card_w, card_h, angle)
        else:
            if back_img is None:
                continue
            # Render back layer with same sizing/rotation rules
            img = _render_layer_cached(str(repo_root / deck.back_image), card_w, card_h, angle)  # type: ignore[arg-type]

        # Determine paste position
        if s.anchor == "topleft":
            if s.x is None or s.y is None:
                continue
            px = int(round(s.x * scale))
            py = int(round(s.y * scale))
        else:  # default: center
            if s.cx is None or s.cy is None:
                continue
            cx = float(s.cx) * scale
            cy = float(s.cy) * scale
            px = int(round(cx - img.size[0] / 2))
            py = int(round(cy - img.size[1] / 2))

        canvas.alpha_composite(img, dest=(px, py))

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True, compress_level=6)
    return RenderResult(png_bytes=out.getvalue(), width=canvas_w, height=canvas_h)


def prepare_download_png(
    *,
    png_bytes: bytes,
    watermark_text: str = "Tarozon.com",
    max_side: int = 1080,
    opacity: int = 72,  # 0-255
    padding: int = 18,
    compress_level: int = 9,
) -> tuple[bytes, int, int]:
    """
    Prepare PNG for sharing:
    - Downscale to max_side on the longest edge (mobile-friendly)
    - Add semi-transparent watermark (bottom-right)
    - Save PNG with strong compression
    Returns: (png_bytes, width, height)
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    # Watermark overlay
    if watermark_text.strip():
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Font: try DejaVuSans, fallback to default bitmap font
        font_size = max(14, int(round(min(img.size) * 0.035)))
        font = None
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

        # Measure text
        bbox = draw.textbbox((0, 0), watermark_text, font=font, stroke_width=2)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        x = img.size[0] - padding - tw
        y = img.size[1] - padding - th
        x = max(padding, x)
        y = max(padding, y)

        # Subtle shadow/outline for readability
        fill = (255, 255, 255, int(opacity))
        stroke = (0, 0, 0, int(min(255, opacity + 60)))
        draw.text((x, y), watermark_text, font=font, fill=fill, stroke_width=2, stroke_fill=stroke)

        img = Image.alpha_composite(img, overlay)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True, compress_level=compress_level)
    out_bytes = out.getvalue()
    fw, fh = Image.open(io.BytesIO(out_bytes)).size
    return out_bytes, fw, fh

