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


def _draw_placeholder_frame(card_w: int, card_h: int, label: str, angle: int) -> Image.Image:
    """이미지 파일이 없을 때 카드 번호·이름이 적힌 세련된 빈 프레임을 그림."""
    img = Image.new("RGBA", (card_w, card_h), (245, 240, 232, 255))  # #f5f0e8
    draw = ImageDraw.Draw(img)
    border = 2
    draw.rectangle(
        [(border, border), (card_w - border - 1, card_h - border - 1)],
        outline=(120, 115, 105, 255),
        width=border,
    )
    try:
        font_size = max(12, min(card_w, card_h) // 12)
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", max(10, min(card_w, card_h) // 14))
        except Exception:
            font = ImageFont.load_default()
    text = label if len(label) <= 24 else label[:21] + "..."
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (card_w - tw) // 2
    ty = (card_h - th) // 2
    draw.text((tx, ty), text, font=font, fill=(60, 55, 50, 255))
    return _rotate_rgba(img, angle)


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
            try:
                back_img = _load_image_cached(str(back_path))
            except Exception:
                back_img = None

    # Render slots by z order
    render_slots = sorted(layout.slots, key=lambda s: (s.z, s.key))
    for s in render_slots:
        code = codes_by_slot.get(s.key)

        angle = int(angles_by_slot.get(s.key, 0))
        if code:
            img_path = repo_root / deck.image_dir / f"{code}.jpg"
            try:
                img = _render_layer_cached(str(img_path), card_w, card_h, angle)
            except Exception:
                card = deck.card_by_code(code)
                label = f"{code}. {card.display_name}" if card else str(code)
                img = _draw_placeholder_frame(card_w, card_h, label, angle)
        else:
            if back_img is None:
                continue
            try:
                img = _render_layer_cached(str(repo_root / deck.back_image), card_w, card_h, angle)  # type: ignore[arg-type]
            except Exception:
                continue

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


def _find_serif_font(font_size: int) -> ImageFont.FreeTypeFont:
    """DejaVu Serif 우선, 없으면 시스템 serif/sans 순차 시도. Linux/Windows/macOS 대응."""
    candidates = [
        # Linux (Debian/Ubuntu, AWS 등)
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSerif.ttf",
        # Linux sans fallback
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/DejaVuSerif.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Times.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, font_size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def prepare_download_png(
    *,
    png_bytes: bytes,
    watermark_text: str = "Tarozon.com",
    max_side: int = 1080,
    opacity: int = 255,
    padding: int = 18,
    compress_level: int = 9,
    frame_padding: int = 24,
    border_width: int = 6,
) -> tuple[bytes, int, int]:
    """
    Prepare PNG for sharing (Grand Budapest theme):
    - Downscale to max_side on the longest edge (mobile-friendly)
    - Add frame (ivory background, gold double border)
    - Add watermark (Hotel Gold, bottom-right)
    Returns: (png_bytes, width, height)
    """
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    # Frame: ivory background, gold double border
    bg_color = (255, 254, 248, 255)  # #FFFEF8
    gold = (212, 175, 55, 255)  # #D4AF37
    new_w = img.width + 2 * frame_padding
    new_h = img.height + 2 * frame_padding
    canvas = Image.new("RGBA", (new_w, new_h), bg_color)
    canvas.paste(img, (frame_padding, frame_padding))

    draw = ImageDraw.Draw(canvas)
    # Outer border
    draw.rectangle(
        [(0, 0), (new_w - 1, new_h - 1)],
        outline=gold,
        width=border_width,
    )
    # Inner border (double effect)
    gap = border_width
    draw.rectangle(
        [(gap, gap), (new_w - 1 - gap, new_h - 1 - gap)],
        outline=gold,
        width=border_width,
    )

    # Watermark overlay
    if watermark_text.strip():
        font_size = max(14, int(round(min(canvas.size) * 0.04)))
        font = _find_serif_font(font_size)

        bbox = draw.textbbox((0, 0), watermark_text, font=font, stroke_width=2)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        wm_pad = padding + frame_padding
        x = canvas.size[0] - wm_pad - tw
        y = canvas.size[1] - wm_pad - th
        x = max(wm_pad, x)
        y = max(wm_pad, y)

        fill = (212, 175, 55, int(opacity))  # Hotel Gold
        stroke = (60, 50, 30, 255)  # Dark brown
        draw.text((x, y), watermark_text, font=font, fill=fill, stroke_width=2, stroke_fill=stroke)

    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True, compress_level=compress_level)
    out_bytes = out.getvalue()
    fw, fh = Image.open(io.BytesIO(out_bytes)).size
    return out_bytes, fw, fh

