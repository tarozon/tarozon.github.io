from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from PIL import Image

from tarozon_core.compose import compose_spread_image, prepare_download_png
from tarozon_core.decks import Deck, load_decks
from tarozon_core.draw import draw_many, draw_one
from tarozon_core.prompts import build_prompt_cards_with_labels
from tarozon_core.spreads import Spread, load_spreads

try:
    from streamlit_image_coordinates import streamlit_image_coordinates  # type: ignore
except Exception:  # pragma: no cover
    streamlit_image_coordinates = None


@dataclass
class DrawState:
    deck_id: str
    spread_id: str
    codes: list[str | None]
    angles: list[int]


REPO_ROOT = Path(__file__).resolve().parent


def _timestamp_slug(prefix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}"


def _allowed_angles_for_slot(spread: Spread, idx: int) -> list[int]:
    slot = spread.slots[idx]
    angles = list(getattr(slot, "allowed_angles", None) or []) or [0, 180]
    # layout override wins
    if spread.layout is not None:
        ls = spread.layout.slot_by_key(slot.key)
        if ls is not None and ls.allowed_angles:
            angles = list(ls.allowed_angles)
    return angles or [0, 180]


def _default_angle_for_slot(spread: Spread, idx: int) -> int:
    angles = _allowed_angles_for_slot(spread, idx)
    return int(angles[0]) if angles else 0


def _random_angle_for_slot(spread: Spread, idx: int) -> int:
    import random

    return int(random.choice(_allowed_angles_for_slot(spread, idx)))


def _toggle_angle(spread: Spread, idx: int, current: int) -> int:
    angles = _allowed_angles_for_slot(spread, idx)
    if current not in angles:
        return int(angles[0])
    pos = angles.index(current)
    return int(angles[(pos + 1) % len(angles)])


def _slot_index_by_key(spread: Spread, key: str) -> int | None:
    for i, s in enumerate(spread.slots):
        if s.key == key:
            return i
    return None


def _hit_test_slot_key(*, spread: Spread, angles: list[int], x: int, y: int) -> str | None:
    if spread.layout is None or spread.layout.type != "absolute":
        return None

    layout = spread.layout
    scale = float(layout.scale or 1.0)
    base_w = float(layout.card.width) * scale
    base_h = float(layout.card.height) * scale

    # Higher z should win when overlapping
    ordered = sorted(layout.slots, key=lambda s: (s.z, s.key), reverse=True)
    for ls in ordered:
        idx = _slot_index_by_key(spread, ls.key)
        if idx is None:
            continue

        angle = int(angles[idx] if idx < len(angles) else 0) % 360
        w = base_h if angle in (90, 270) else base_w
        h = base_w if angle in (90, 270) else base_h

        if ls.anchor == "topleft":
            if ls.x is None or ls.y is None:
                continue
            left = float(ls.x) * scale
            top = float(ls.y) * scale
        else:  # center
            if ls.cx is None or ls.cy is None:
                continue
            cx = float(ls.cx) * scale
            cy = float(ls.cy) * scale
            left = cx - (w / 2.0)
            top = cy - (h / 2.0)

        if left <= x <= left + w and top <= y <= top + h:
            return ls.key

    return None


def _encode_state(ds: DrawState) -> str:
    payload = {"d": ds.deck_id, "s": ds.spread_id, "c": ds.codes, "a": ds.angles}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_state(encoded: str) -> dict[str, Any] | None:
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _get_query_state() -> str | None:
    if hasattr(st, "query_params"):
        v = st.query_params.get("state")
        if isinstance(v, list):
            return v[0] if v else None
        return str(v) if v is not None else None
    qp = st.experimental_get_query_params()
    v = qp.get("state")
    return v[0] if v else None


def _set_query_state(encoded: str) -> None:
    if hasattr(st, "query_params"):
        cur = _get_query_state()
        if cur != encoded:
            # Preserve other query params; only update "state"
            st.query_params["state"] = encoded
    else:
        cur = _get_query_state()
        if cur != encoded:
            st.experimental_set_query_params(state=encoded)


def _normalize_state(ds: DrawState, *, deck_id: str, spread: Spread) -> DrawState:
    codes = list(ds.codes) if isinstance(ds.codes, list) else []
    angles = list(ds.angles) if isinstance(ds.angles, list) else []

    if len(codes) != spread.n_cards:
        codes = [None for _ in range(spread.n_cards)]
    if len(angles) != spread.n_cards:
        angles = [_default_angle_for_slot(spread, i) for i in range(spread.n_cards)]

    # Ensure default angle is valid per-slot (especially Celtic slot2)
    for i in range(spread.n_cards):
        allowed = _allowed_angles_for_slot(spread, i)
        if angles[i] not in allowed:
            angles[i] = _default_angle_for_slot(spread, i)

    return DrawState(deck_id=deck_id, spread_id=spread.id, codes=codes, angles=angles)


@st.cache_data(show_spinner=False, max_entries=256)
def _render_board_png(
    *,
    repo_root_str: str,
    deck_id: str,
    spread_id: str,
    spread_mtime: int,
    codes: tuple[str | None, ...],
    angles: tuple[int, ...],
) -> tuple[bytes, int, int]:
    repo_root = Path(repo_root_str)
    decks = load_decks(repo_root)
    spreads = load_spreads(repo_root)
    deck = decks[deck_id]
    spread = spreads[spread_id]

    codes_by_slot = {
        slot.key: code
        for slot, code in zip(spread.slots, list(codes), strict=False)
        if code
    }
    angles_by_slot = {slot.key: int(angles[i]) for i, slot in enumerate(spread.slots)}
    rendered = compose_spread_image(
        repo_root=repo_root,
        deck=deck,
        spread=spread,
        codes_by_slot=codes_by_slot,
        angles_by_slot=angles_by_slot,
        render_back_for_missing=True,
    )
    return rendered.png_bytes, rendered.width, rendered.height


@st.cache_data(show_spinner=False, max_entries=256)
def _download_png_bytes(png_bytes: bytes, spread_id: str, deck_id: str) -> tuple[bytes, str]:
    # Mobile-share optimized download (watermark + downscale + stronger compression)
    out_bytes, w, h = prepare_download_png(
        png_bytes=png_bytes,
        watermark_text="Tarozon.com",
        max_side=1080,
        opacity=72,
        padding=18,
        compress_level=9,
    )
    size_kb = len(out_bytes) / 1024.0
    meta = f"{w}×{h} · {size_kb:.0f}KB"
    return out_bytes, meta


st.set_page_config(page_title="TAROZON", page_icon="🔮", layout="centered")

decks = load_decks(REPO_ROOT)
spreads = load_spreads(REPO_ROOT)
if not decks or not spreads:
    st.error("덱/스프레드 데이터를 찾지 못했어요. `data/decks`, `data/spreads` 확인이 필요합니다.")
    st.stop()

default_deck_id = "rws" if "rws" in decks else sorted(decks.keys())[0]
default_spread_id = "one_card" if "one_card" in spreads else sorted(spreads.keys())[0]

# Load state from URL (refresh-safe)
qs = _get_query_state()
loaded: DrawState | None = None
if qs:
    obj = _decode_state(qs)
    if obj:
        d = obj.get("d")
        s = obj.get("s")
        if isinstance(d, str) and d in decks and isinstance(s, str) and s in spreads:
            loaded = DrawState(deck_id=d, spread_id=s, codes=list(obj.get("c", [])), angles=list(obj.get("a", [])))

if "draw_state" not in st.session_state:
    if loaded:
        st.session_state.draw_state = loaded
    else:
        st.session_state.draw_state = DrawState(
            deck_id=default_deck_id,
            spread_id=default_spread_id,
            codes=[None for _ in range(spreads[default_spread_id].n_cards)],
            angles=[_default_angle_for_slot(spreads[default_spread_id], i) for i in range(spreads[default_spread_id].n_cards)],
        )

if "question" not in st.session_state:
    st.session_state.question = ""

# Click debouncing (prevents repeated processing on reruns)
if "last_click_unix_time" not in st.session_state:
    st.session_state.last_click_unix_time = {}

deck: Deck = decks.get(st.session_state.draw_state.deck_id, decks[default_deck_id])
spread: Spread = spreads.get(st.session_state.draw_state.spread_id, spreads[default_spread_id])
st.session_state.draw_state = _normalize_state(st.session_state.draw_state, deck_id=deck.id, spread=spread)

st.title("🔮 TAROZON")
st.caption("모든 카드 선택/표시는 ‘스프레드 보드’에서만 진행됩니다. (빈 곳 클릭=뽑기, 이미 뽑힌 카드 클릭=FLIP)")

with st.sidebar:
    st.header("설정")
    deck_options = {d.name: d.id for d in decks.values()}
    spread_options = {s.name: s.id for s in spreads.values()}

    sel_deck_name = st.selectbox(
        "덱",
        options=sorted(deck_options.keys()),
        index=sorted(deck_options.keys()).index(next(n for n, did in deck_options.items() if did == deck.id)),
    )
    sel_spread_name = st.selectbox(
        "스프레드",
        options=sorted(spread_options.keys()),
        index=sorted(spread_options.keys()).index(next(n for n, sid in spread_options.items() if sid == spread.id)),
    )

    if st.button("🧹 전체 리셋", use_container_width=True):
        new_spread = spreads[spread_options[sel_spread_name]]
        st.session_state.draw_state = DrawState(
            deck_id=deck_options[sel_deck_name],
            spread_id=new_spread.id,
            codes=[None for _ in range(new_spread.n_cards)],
            angles=[_default_angle_for_slot(new_spread, i) for i in range(new_spread.n_cards)],
        )
        _set_query_state(_encode_state(st.session_state.draw_state))
        st.rerun()

    if st.button("🎴 DRAW ALL(남은 슬롯)", use_container_width=True, type="primary"):
        target_spread = spreads[spread_options[sel_spread_name]]
        if st.session_state.draw_state.spread_id != target_spread.id or st.session_state.draw_state.deck_id != deck_options[sel_deck_name]:
            st.session_state.draw_state = DrawState(
                deck_id=deck_options[sel_deck_name],
                spread_id=target_spread.id,
                codes=[None for _ in range(target_spread.n_cards)],
                angles=[_default_angle_for_slot(target_spread, i) for i in range(target_spread.n_cards)],
            )

        deck = decks[st.session_state.draw_state.deck_id]
        spread = spreads[st.session_state.draw_state.spread_id]

        existing = [c for c in st.session_state.draw_state.codes if c]
        need = sum(1 for c in st.session_state.draw_state.codes if c is None)
        new_codes = draw_many(deck, need, exclude_codes=existing) if need else []
        it = iter(new_codes)
        for i in range(spread.n_cards):
            if st.session_state.draw_state.codes[i] is None:
                st.session_state.draw_state.codes[i] = next(it)
                st.session_state.draw_state.angles[i] = _random_angle_for_slot(spread, i)
        _set_query_state(_encode_state(st.session_state.draw_state))
        st.rerun()

    # Apply deck/spread selection immediately (no extra UI blocks)
    new_deck_id = deck_options[sel_deck_name]
    new_spread_id = spread_options[sel_spread_name]
    if new_deck_id != deck.id or new_spread_id != spread.id:
        ns = spreads[new_spread_id]
        st.session_state.draw_state = DrawState(
            deck_id=new_deck_id,
            spread_id=new_spread_id,
            codes=[None for _ in range(ns.n_cards)],
            angles=[_default_angle_for_slot(ns, i) for i in range(ns.n_cards)],
        )
        _set_query_state(_encode_state(st.session_state.draw_state))
        st.rerun()

deck = decks[st.session_state.draw_state.deck_id]
spread = spreads[st.session_state.draw_state.spread_id]

if spread.layout is None:
    st.error("이 스프레드는 아직 좌표 레이아웃이 없어서 보드 모드로 표시할 수 없어요.")
    st.stop()

spread_path = REPO_ROOT / "data" / "spreads" / f"{spread.id}.json"
spread_mtime = int(spread_path.stat().st_mtime) if spread_path.exists() else 0

png_bytes, img_w, img_h = _render_board_png(
    repo_root_str=str(REPO_ROOT),
    deck_id=deck.id,
    spread_id=spread.id,
    spread_mtime=spread_mtime,
    codes=tuple(st.session_state.draw_state.codes),
    angles=tuple(int(a) for a in st.session_state.draw_state.angles),
)

st.subheader(f"🧿 {spread.name} 보드")

click = None
if streamlit_image_coordinates is not None:
    pil_img = Image.open(io.BytesIO(png_bytes))
    click = streamlit_image_coordinates(
        pil_img,
        key=f"board_{spread.id}_{deck.id}",
        use_column_width="always",
        cursor="pointer",
    )
else:
    st.image(png_bytes, use_container_width=True)
    st.warning("클릭 드로우 기능을 위해 `streamlit-image-coordinates` 설치가 필요해요. `pip install -r requirements.txt`")

board_key = f"{spread.id}:{deck.id}"
click_time = None
if click and isinstance(click, dict):
    click_time = click.get("unix_time")

should_process_click = False
if isinstance(click_time, (int, float)):
    last = st.session_state.last_click_unix_time.get(board_key)
    # Only process if it's a NEW click event
    if last is None or float(click_time) > float(last):
        should_process_click = True

if (
    should_process_click
    and click
    and isinstance(click, dict)
    and "x" in click
    and "y" in click
    and "width" in click
    and "height" in click
):
    # Convert display coords -> original image coords
    disp_w = float(click.get("width") or 1)
    disp_h = float(click.get("height") or 1)
    x = int(round(float(click["x"]) * (img_w / disp_w)))
    y = int(round(float(click["y"]) * (img_h / disp_h)))

    slot_key = _hit_test_slot_key(spread=spread, angles=st.session_state.draw_state.angles, x=x, y=y)
    if slot_key is not None:
        idx = _slot_index_by_key(spread, slot_key)
        if idx is not None:
            if st.session_state.draw_state.codes[idx] is None:
                used = [c for j, c in enumerate(st.session_state.draw_state.codes) if c and j != idx]
                st.session_state.draw_state.codes[idx] = draw_one(deck, exclude_codes=used)
                st.session_state.draw_state.angles[idx] = _random_angle_for_slot(spread, idx)
            else:
                # Click on existing card => FLIP
                st.session_state.draw_state.angles[idx] = _toggle_angle(
                    spread, idx, int(st.session_state.draw_state.angles[idx])
                )
            # Mark click as processed BEFORE rerun to prevent flip loops
            st.session_state.last_click_unix_time[board_key] = float(click_time)
            _set_query_state(_encode_state(st.session_state.draw_state))
            st.rerun()
    # If click didn't hit any slot, still mark processed to avoid repeated attempts
    st.session_state.last_click_unix_time[board_key] = float(click_time)

download_bytes, download_meta = _download_png_bytes(png_bytes, spread.id, deck.id)
st.caption(f"다운로드 최적화: {download_meta}")
st.download_button(
    "⬇️ 보드 이미지 다운로드(PNG)",
    data=download_bytes,
    file_name=f"{_timestamp_slug('tarozon-spread')}-{spread.id}-{deck.id}.png",
    mime="image/png",
    use_container_width=True,
    key=f"download_board_{spread.id}_{deck.id}",
)

st.markdown("---")
st.subheader("📝 GPT 리딩 요청문")
st.text_area(
    "질문 입력",
    key="question",
    placeholder="예: 오늘의 조언을 알려줘. / 상대방의 마음이 궁금해.",
    height=120,
)

prompt_text = ""
cards = []
flags = []
ready = True
for i in range(spread.n_cards):
    code = st.session_state.draw_state.codes[i]
    if not code:
        ready = False
        break
    card = deck.card_by_code(code)
    if not card:
        ready = False
        break
    cards.append(card)
    # Only 180° is treated as "reversed" for prompt purposes
    flags.append(int(st.session_state.draw_state.angles[i] or 0) % 360 == 180)

if ready:
    prompt_text = build_prompt_cards_with_labels(
        question=st.session_state.question,
        deck=deck,
        spread=spread,
        cards=cards,
        reversed_flags=flags,
    )
else:
    st.info("리딩 요청문은 모든 슬롯에 카드가 채워지면 자동 생성돼요.")

st.text_area("생성된 요청문", value=prompt_text, height=240)

st.download_button(
    "📄 요청문 다운로드(TXT)",
    data=(prompt_text or "").encode("utf-8"),
    file_name=f"{_timestamp_slug('tarozon-prompt')}.txt",
    mime="text/plain; charset=utf-8",
    use_container_width=True,
    disabled=not bool(prompt_text.strip()),
)

# URL은 "사용자 액션(클릭/DRAW ALL/리셋/선택 변경)"에서만 업데이트합니다.