from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import base64
import io
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from tarozon_core.compose import compose_spread_image, prepare_download_png
from tarozon_core.decks import Deck, load_decks
from tarozon_core.draw import draw_many, draw_one
from tarozon_core.prompts import build_prompt_cards_with_labels
from tarozon_core.rooms import ChatManager, RoomManager
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


def _allowed_angles_for_slot(spread: Spread, idx: int, deck: Deck | None = None) -> list[int]:
    if deck is not None and not deck.reversible:
        return [0]
    slot = spread.slots[idx]
    angles = list(getattr(slot, "allowed_angles", None) or []) or [0, 180]
    # layout override wins
    if spread.layout is not None:
        ls = spread.layout.slot_by_key(slot.key)
        if ls is not None and ls.allowed_angles:
            angles = list(ls.allowed_angles)
    return angles or [0, 180]


def _default_angle_for_slot(spread: Spread, idx: int, deck: Deck | None = None) -> int:
    angles = _allowed_angles_for_slot(spread, idx, deck)
    return int(angles[0]) if angles else 0


def _random_angle_for_slot(spread: Spread, idx: int, deck: Deck | None = None) -> int:
    import random

    return int(random.choice(_allowed_angles_for_slot(spread, idx, deck)))


def _toggle_angle(spread: Spread, idx: int, current: int, deck: Deck | None = None) -> int:
    angles = _allowed_angles_for_slot(spread, idx, deck)
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


def _draw_state_to_dict(ds: DrawState) -> dict[str, Any]:
    """Same payload as _encode_state, for DB storage."""
    return {"d": ds.deck_id, "s": ds.spread_id, "c": ds.codes, "a": ds.angles}


def _get_query_state() -> str | None:
    if hasattr(st, "query_params"):
        v = st.query_params.get("state")
        if isinstance(v, list):
            return v[0] if v else None
        return str(v) if v is not None else None
    qp = st.experimental_get_query_params()
    v = qp.get("state")
    return v[0] if v else None


def _get_admin_param() -> str | None:
    if hasattr(st, "query_params"):
        v = st.query_params.get("admin")
        return v[0] if isinstance(v, list) and v else (str(v) if v else None)
    qp = st.experimental_get_query_params()
    v = qp.get("admin")
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


def _normalize_state(ds: DrawState, *, deck_id: str, spread: Spread, deck: Deck | None = None) -> DrawState:
    codes = list(ds.codes) if isinstance(ds.codes, list) else []
    angles = list(ds.angles) if isinstance(ds.angles, list) else []

    if len(codes) != spread.n_cards:
        codes = [None for _ in range(spread.n_cards)]
    if len(angles) != spread.n_cards:
        angles = [_default_angle_for_slot(spread, i, deck) for i in range(spread.n_cards)]

    # Ensure default angle is valid per-slot (especially Celtic slot2)
    for i in range(spread.n_cards):
        allowed = _allowed_angles_for_slot(spread, i, deck)
        if angles[i] not in allowed:
            angles[i] = _default_angle_for_slot(spread, i, deck)

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
        padding=18,
        compress_level=9,
    )
    size_kb = len(out_bytes) / 1024.0
    meta = f"{w}×{h} · {size_kb:.0f}KB"
    return out_bytes, meta


st.set_page_config(
    page_title="TAROZON",
    page_icon=str(REPO_ROOT / "legacy" / "favicon.ico"),
    layout="centered",
)

# Grand Budapest Hotel theme - global CSS
st.markdown(
    """
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond&family=Playfair+Display&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
    <style>
    /* Main background - Mendl's Pink */
    .stApp { background-color: #F4C2C2 !important; }
    /* Wes Anderson symmetry - center align, equal margins */
    .main .block-container { max-width: 900px; margin: 0 auto; padding: 2rem; }
    /* Typography: Cormorant Garamond (body), Playfair Display (headings) - 텍스트 요소만 (아이콘 제외) */
    .main .stMarkdown,
    .main .stMarkdown *,
    .main label,
    .main p,
    .main [data-testid="stMarkdown"] {
      font-family: 'Cormorant Garamond', Georgia, serif !important;
    }
    h1, h2, h3, .stSubheader, [data-testid="stMarkdown"] h1, [data-testid="stMarkdown"] h2, [data-testid="stMarkdown"] h3 { font-family: 'Playfair Display', Georgia, serif !important; }
    /* Buttons: rectangular, gold border */
    button, .stButton button, [data-testid="stButton"] button,
    a[data-testid="stLinkButton"] {
        border-radius: 0 !important;
        border: 2px solid #D4AF37 !important;
        background-color: #A2231D !important;
        color: white !important;
    }
    /* Sidebar: Vintage Plum + Muted Ivory - 텍스트 요소만 폰트 적용 (.stMarkdown * 제외로 아이콘 보호) */
    [data-testid="stSidebar"] { background-color: #7C5C85 !important; }
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stTextInput label {
      font-family: 'Cormorant Garamond', Georgia, serif !important;
      color: #FDF5E6 !important;
    }
    /* Sidebar collapse/expand 아이콘 - Material Icons 복구 (범위 확대) */
    [data-testid="stSidebar"] > div:first-child > button,
    [data-testid="stSidebar"] button[aria-label*="collapse"],
    [data-testid="stSidebar"] button[aria-label*="expand"],
    [data-testid="stSidebar"] button[aria-label*="arrow"],
    [aria-label*="collapse"],
    [aria-label*="expand"],
    [data-testid="stSidebar"] [class*="collapse"] {
      font-family: 'Material Icons', 'Material Symbols Outlined', 'Material Icons Outlined', sans-serif !important;
    }
    [data-testid="stSidebar"] > div:first-child > button *,
    [data-testid="stSidebar"] button[aria-label*="collapse"] *,
    [data-testid="stSidebar"] button[aria-label*="expand"] * {
      font-family: inherit !important;
    }
    /* Dividers: Hotel Gold */
    hr { border-color: #D4AF37 !important; }
    /* Card board frame - Grand Budapest hotel style */
    .st-key-board_frame,
    .st-key-board_frame_viewer {
      border: 6px double #D4AF37 !important;
      padding: 1.5rem !important;
      background-color: #FFFEF8 !important;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12) !important;
      border-radius: 0 !important;
    }
    /* Viewer frame - position relative for potential overlays */
    .st-key-board_frame_viewer {
      position: relative !important;
    }
    /* 전체화면 버튼 항상 표시 (호버 없이) */
    .st-key-board_frame_viewer button[title="View fullscreen"] {
      opacity: 1 !important;
      transform: scale(1) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

decks = load_decks(REPO_ROOT)
spreads = load_spreads(REPO_ROOT)
if not decks or not spreads:
    st.error("Deck/spread data not found. Please check `data/decks` and `data/spreads`.")
    st.stop()

DECK_DISPLAY_ORDER = ("rws", "iching", "thoth", "holitzka")
MASTER_ONLY_DECK_IDS = ("holitzka", "thoth")
default_deck_id = "rws" if "rws" in decks else sorted(decks.keys())[0]
default_spread_id = "one_card" if "one_card" in spreads else sorted(spreads.keys())[0]

_room_manager: RoomManager | None = None
_chat_manager: ChatManager | None = None


def get_room_manager() -> RoomManager:
    """솔로 모드에서는 호출하지 않음. 방 생성/입장 시에만 사용해 Supabase 클라이언트를 지연 생성."""
    global _room_manager
    if _room_manager is None:
        _room_manager = RoomManager()
    return _room_manager


def get_chat_manager() -> ChatManager:
    """솔로 모드에서는 호출하지 않음. 방에 입장한 뒤 채팅 시에만 사용해 지연 생성."""
    global _chat_manager
    if _chat_manager is None:
        _chat_manager = ChatManager()
    return _chat_manager


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
        default_deck = decks[default_deck_id]
        default_spread = spreads[default_spread_id]
        st.session_state.draw_state = DrawState(
            deck_id=default_deck_id,
            spread_id=default_spread_id,
            codes=[None for _ in range(default_spread.n_cards)],
            angles=[_default_angle_for_slot(default_spread, i, default_deck) for i in range(default_spread.n_cards)],
        )

if "question" not in st.session_state:
    st.session_state.question = ""

if "host_room_code" not in st.session_state:
    st.session_state.host_room_code = None
if "viewer_mode" not in st.session_state:
    st.session_state.viewer_mode = False
if "viewer_room_code" not in st.session_state:
    st.session_state.viewer_room_code = None
if "last_viewer_state_json" not in st.session_state:
    st.session_state.last_viewer_state_json = None
if "chat_nickname" not in st.session_state:
    import random
    st.session_state.chat_nickname = "Guest" + str(random.randint(1, 99)).zfill(2)

# room 쿼리 파라미터로 자동 입장
room_param = None
if hasattr(st, "query_params"):
    v = st.query_params.get("room")
    room_param = v[0] if isinstance(v, list) and v else (str(v) if v else None)
else:
    qp = st.experimental_get_query_params()
    v = qp.get("room")
    room_param = v[0] if v else None

if room_param and (rp := room_param.strip().upper()):
    st.session_state.viewer_mode = True
    st.session_state.viewer_room_code = rp
    st.session_state.host_room_code = None

# Viewer: fetch room and restore draw_state (방 있을 때만 get_room_manager 호출 → 솔로 모드에서는 미호출)
if st.session_state.get("viewer_mode") and st.session_state.get("viewer_room_code"):
    rm = get_room_manager()
    room = rm.get_room(st.session_state.viewer_room_code) if rm.is_available else None
    if room is None:
        st.session_state.viewer_mode = False
        st.session_state.viewer_room_code = None
        st.session_state.last_viewer_state_json = None
        st.rerun()
    else:
        obj = room.get("state_json")
        if isinstance(obj, dict):
            d = obj.get("d")
            s = obj.get("s")
            if isinstance(d, str) and d in decks and isinstance(s, str) and s in spreads:
                st.session_state.draw_state = DrawState(
                    deck_id=d,
                    spread_id=s,
                    codes=list(obj.get("c", [])),
                    angles=list(obj.get("a", [])),
                )

# Click debouncing (prevents repeated processing on reruns)
if "last_click_unix_time" not in st.session_state:
    st.session_state.last_click_unix_time = {}

deck: Deck = decks.get(st.session_state.draw_state.deck_id, decks[default_deck_id])
spread: Spread = spreads.get(st.session_state.draw_state.spread_id, spreads[default_spread_id])
st.session_state.draw_state = _normalize_state(st.session_state.draw_state, deck_id=deck.id, spread=spread, deck=deck)

def _sync_host_room_if_any() -> None:
    """host_room_code가 없으면 Supabase 호출 없음(솔로 모드). 방이 있을 때만 DB에 비동기로 저장해 UI가 멈추지 않게 함."""
    if st.session_state.get("host_room_code"):
        rm = get_room_manager()
        if rm.is_available:
            state_dict = _draw_state_to_dict(st.session_state.draw_state)
            room_code = st.session_state.host_room_code
            threading.Thread(
                target=lambda: rm.update_room(room_code, state_dict),
                daemon=True,
            ).start()


_CHAT_MESSAGE_CONTAINER_HEIGHT = 250


def _render_chat_expander(room_code: str, key_prefix: str = "chat", fragment_scope: bool = False) -> None:
    """Render 실시간 채팅 expander: nickname input, fixed-height message list, chat_input. Uses key_prefix for widget keys."""
    if not room_code or not get_chat_manager().is_available:
        return
    nick_key = f"{key_prefix}_nickname"
    if nick_key in st.session_state and st.session_state[nick_key]:
        st.session_state.chat_nickname = str(st.session_state[nick_key]).strip() or st.session_state.chat_nickname
    with st.expander("Concierge Messages", expanded=True):
        st.text_input("Guest Name", value=st.session_state.chat_nickname, key=nick_key)
        messages = get_chat_manager().get_messages(room_code, limit=20)
        try:
            chat_container = st.container(height=_CHAT_MESSAGE_CONTAINER_HEIGHT, key="tarozon_chat_messages")
        except TypeError:
            chat_container = st.container(key="tarozon_chat_messages")
        with chat_container:
            st.markdown(
                """
                <style>
                /* 1) 여백 축소 - 채팅 메시지 버블 (조밀) */
                .st-key-tarozon_chat_messages [data-testid="stChatMessage"],
                .st-key-tarozon_chat_messages .stChatMessage {
                  padding: 0.2rem 0.4rem; margin: 0.12rem 0;
                  background-color: white !important;
                  border-left: 4px solid #D4AF37 !important;
                  border-radius: 0;
                }
                /* 2) 내 메시지 오른쪽 정렬 (wrapper) */
                .st-key-tarozon_chat_messages [class*="chat_msg_"][class*="_mine"] { margin-left: auto; width: fit-content; max-width: 85%; }
                /* 3) 내 메시지 - Hotel Gold accent */
                .st-key-tarozon_chat_messages [class*="chat_msg_"][class*="_mine"] [data-testid="stChatMessage"],
                .st-key-tarozon_chat_messages [class*="chat_msg_"][class*="_mine"] .stChatMessage {
                  background-color: #fff9e6 !important;
                  border-left: 4px solid #D4AF37 !important;
                  border-radius: 0;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            st.html(
                """
                <script>
                (function() {
                    const targetNode = document.querySelector('.st-key-tarozon_chat_messages');
                    if (!targetNode) return;

                    const scrollFull = () => {
                        const scrollable = [targetNode, ...targetNode.querySelectorAll('*')]
                            .filter(el => el.scrollHeight > el.clientHeight);
                        scrollable.forEach(el => {
                            el.scrollTo({ top: el.scrollHeight, behavior: 'auto' });
                        });
                    };

                    scrollFull();

                    const observer = new MutationObserver(() => {
                        scrollFull();
                        setTimeout(scrollFull, 50);
                    });

                    observer.observe(targetNode, { childList: true, subtree: true });
                })();
                </script>
                """,
                unsafe_allow_javascript=True,
            )
            current_nick = (st.session_state.chat_nickname or "").strip()
            for i, msg in enumerate(messages):
                msg_user = (msg.get("user_name") or "").strip()
                is_mine = msg_user == current_nick
                wrapper_key = f"chat_msg_{i}_mine" if is_mine else f"chat_msg_{i}_other"
                with st.container(key=wrapper_key):
                    display_name = (msg.get("user_name") or "").strip()[:7] or "?"
                    with st.chat_message(name=display_name[:1]):
                        st.markdown(f"**{display_name}**: {msg['content']}")
        prompt = st.chat_input("Compose your message...", key=f"{key_prefix}_input")
        if prompt and (prompt := str(prompt).strip()):
            display_name = st.session_state.get(nick_key, st.session_state.chat_nickname) or st.session_state.chat_nickname
            if get_chat_manager().send_message(room_code, display_name, prompt):
                if fragment_scope:
                    st.rerun(scope="fragment")
                else:
                    st.rerun()
            else:
                st.error("Failed to send message.")


@st.fragment(run_every=timedelta(seconds=3))
def _fragment_viewer_live(room_code: str) -> None:
    """Viewer 전용: 방 최신 상태로 보드 + 고정 높이 채팅만 부분 갱신."""
    if not room_code:
        return
    rm = get_room_manager()
    if not rm.is_available:
        return
    room = rm.get_room(room_code)
    if room is None:
        st.error("Room not found.")
        return
    obj = room.get("state_json")
    if not isinstance(obj, dict):
        return
    d = obj.get("d")
    s = obj.get("s")
    if not isinstance(d, str) or d not in decks or not isinstance(s, str) or s not in spreads:
        return
    deck = decks[d]
    spread = spreads[s]
    ds = DrawState(deck_id=d, spread_id=s, codes=list(obj.get("c", [])), angles=list(obj.get("a", [])))
    spread_path = REPO_ROOT / "data" / "spreads" / f"{spread.id}.json"
    spread_mtime = int(spread_path.stat().st_mtime) if spread_path.exists() else 0
    png_bytes, _, _ = _render_board_png(
        repo_root_str=str(REPO_ROOT),
        deck_id=deck.id,
        spread_id=spread.id,
        spread_mtime=spread_mtime,
        codes=tuple(ds.codes),
        angles=tuple(int(a) for a in ds.angles),
    )
    with st.container(key="board_frame_viewer"):
        st.image(png_bytes, use_container_width=True)
    _render_chat_expander(room_code, "chat_viewer", fragment_scope=True)


@st.fragment(run_every=timedelta(seconds=3))
def _fragment_host_chat(room_code: str) -> None:
    """Host 전용: 채팅만 고정 높이로 부분 갱신."""
    _render_chat_expander(room_code, "chat_main", fragment_scope=True)


with st.sidebar:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { padding-top: 0.35rem; margin-top: 0; margin-bottom: 0.2rem; }
        [data-testid="stSidebar"] > div:first-child { padding-top: 0.25rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _favicon_path = REPO_ROOT / "legacy" / "favicon.ico"
    if _favicon_path.exists():
        _favicon_b64 = base64.b64encode(_favicon_path.read_bytes()).decode()
        _favicon_data = f"data:image/x-icon;base64,{_favicon_b64}"
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:0.35rem;">'
            f'<img src="{_favicon_data}" width="28" height="28" style="flex-shrink:0;" />'
            f'<span style="font-size:1.25rem; font-weight:700; color:#FDF5E6;">TAROZON</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("### TAROZON")
    st.markdown("---")
    if not st.session_state.get("viewer_mode"):
        st.header("Settings")
    deck_options = {d.name: d.id for d in decks.values()}
    is_admin = _get_admin_param() == "tarozon1"
    available_deck_ids = [did for did in DECK_DISPLAY_ORDER if did in decks]
    if not is_admin:
        available_deck_ids = [did for did in available_deck_ids if did not in MASTER_ONLY_DECK_IDS]
    ordered_deck_names = [decks[did].name for did in available_deck_ids]
    for did, d in decks.items():
        if d.name not in ordered_deck_names:
            if is_admin or did not in MASTER_ONLY_DECK_IDS:
                ordered_deck_names.append(d.name)
    spread_options = {s.name: s.id for s in spreads.values()}

    if deck.name not in ordered_deck_names:
        fallback_id = (
            "rws"
            if "rws" in decks and "rws" not in MASTER_ONLY_DECK_IDS
            else available_deck_ids[0]
        )
        fallback_deck = decks[fallback_id]
        st.session_state.draw_state = DrawState(
            deck_id=fallback_id,
            spread_id=st.session_state.draw_state.spread_id,
            codes=[None for _ in range(spread.n_cards)],
            angles=[_default_angle_for_slot(spread, i, fallback_deck) for i in range(spread.n_cards)],
        )
        _set_query_state(_encode_state(st.session_state.draw_state))
        _sync_host_room_if_any()
        st.rerun()

    sel_deck_name = st.selectbox(
        "The Library of Decks",
        options=ordered_deck_names,
        index=ordered_deck_names.index(deck.name),
    )
    sel_spread_name = st.selectbox(
        "Arrangement",
        options=sorted(spread_options.keys()),
        index=sorted(spread_options.keys()).index(next(n for n, sid in spread_options.items() if sid == spread.id)),
    )

    if st.button("Shuffle the Cards", use_container_width=True):
        new_spread = spreads[spread_options[sel_spread_name]]
        new_deck = decks[deck_options[sel_deck_name]]
        st.session_state.draw_state = DrawState(
            deck_id=deck_options[sel_deck_name],
            spread_id=new_spread.id,
            codes=[None for _ in range(new_spread.n_cards)],
            angles=[_default_angle_for_slot(new_spread, i, new_deck) for i in range(new_spread.n_cards)],
        )
        _set_query_state(_encode_state(st.session_state.draw_state))
        _sync_host_room_if_any()
        st.rerun()

    if st.button("Unveil All Slots", use_container_width=True, type="primary"):
        target_spread = spreads[spread_options[sel_spread_name]]
        draw_deck = decks[deck_options[sel_deck_name]]
        if st.session_state.draw_state.spread_id != target_spread.id or st.session_state.draw_state.deck_id != deck_options[sel_deck_name]:
            st.session_state.draw_state = DrawState(
                deck_id=deck_options[sel_deck_name],
                spread_id=target_spread.id,
                codes=[None for _ in range(target_spread.n_cards)],
                angles=[_default_angle_for_slot(target_spread, i, draw_deck) for i in range(target_spread.n_cards)],
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
                st.session_state.draw_state.angles[i] = _random_angle_for_slot(spread, i, deck)
        _set_query_state(_encode_state(st.session_state.draw_state))
        _sync_host_room_if_any()
        st.rerun()

    # Apply deck/spread selection immediately (no extra UI blocks)
    new_deck_id = deck_options[sel_deck_name]
    new_spread_id = spread_options[sel_spread_name]
    if new_deck_id != deck.id or new_spread_id != spread.id:
        ns = spreads[new_spread_id]
        new_deck = decks[new_deck_id]
        st.session_state.draw_state = DrawState(
            deck_id=new_deck_id,
            spread_id=new_spread_id,
            codes=[None for _ in range(ns.n_cards)],
            angles=[_default_angle_for_slot(ns, i, new_deck) for i in range(ns.n_cards)],
        )
        _set_query_state(_encode_state(st.session_state.draw_state))
        _sync_host_room_if_any()
        st.rerun()

    st.markdown("---")
    st.subheader("Live Reading Exchange")
    if st.session_state.get("viewer_mode"):
        if st.button("Check-out", use_container_width=True):
            st.session_state.viewer_mode = False
            st.session_state.viewer_room_code = None
            st.session_state.last_viewer_state_json = None
            st.rerun()
    else:
        is_admin = _get_admin_param() == "tarozon1"
        if st.session_state.get("host_room_code"):
            room_code = st.session_state.host_room_code
            base_url = os.environ.get("TAROZON_BASE_URL", "https://tarozon.com").rstrip("/")
            invite_url = f"{base_url}/?room={room_code}"
            st.success(f"Room code: **{room_code}**")
            st.caption("Invitation Link (click to copy)")
            st.code(invite_url, language=None)
        if is_admin:
            if st.button("Create Room", use_container_width=True):
                rm = get_room_manager()
                if not rm.is_available:
                    st.error("Supabase configuration required (SUPABASE_URL, SUPABASE_SERVICE_KEY)")
                else:
                    state_dict = _draw_state_to_dict(st.session_state.draw_state)
                    code = rm.create_room(state_dict)
                    if code:
                        st.session_state.host_room_code = code
                        st.session_state.chat_nickname = "Tarozon"
                        st.rerun()
                    else:
                        st.error("Failed to create room.")
        else:
            st.caption("Please enter your Lobby Access Key")
        join_code_raw = st.text_input("Lobby Access Key", key="room_code_input", placeholder="ABC123")
        join_code = (join_code_raw.strip().upper() if join_code_raw else "") or None
        if st.button("Check-in", use_container_width=True) and join_code:
            rm = get_room_manager()
            if not rm.is_available:
                st.error("Supabase configuration required (SUPABASE_URL, SUPABASE_SERVICE_KEY)")
            else:
                room = rm.get_room(join_code)
                if room is None:
                    st.error("Room not found.")
                else:
                    obj = room.get("state_json")
                    if isinstance(obj, dict):
                        d = obj.get("d")
                        s = obj.get("s")
                        if isinstance(d, str) and d in decks and isinstance(s, str) and s in spreads:
                            st.session_state.viewer_mode = True
                            st.session_state.viewer_room_code = join_code
                            st.session_state.host_room_code = None
                            st.session_state.draw_state = DrawState(
                                deck_id=d,
                                spread_id=s,
                                codes=list(obj.get("c", [])),
                                angles=list(obj.get("a", [])),
                            )
                            st.rerun()
                        else:
                            st.error("Invalid room data.")
                    else:
                        st.error("Invalid room data.")

deck = decks[st.session_state.draw_state.deck_id]
spread = spreads[st.session_state.draw_state.spread_id]

if spread.layout is None:
    st.error("This spread has no coordinate layout and cannot be displayed in board mode.")
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

# 방 코드가 없으면 fragment를 호출하지 않아 3초 주기 갱신이 꺼짐(리소스 절약). 솔로 모드에서는 Supabase/채팅 미사용.
current_room_code = st.session_state.get("host_room_code") or st.session_state.get("viewer_room_code")

if st.session_state.get("viewer_mode") and current_room_code:
    _fragment_viewer_live(current_room_code)
    st.stop()

st.subheader(f"{spread.name} · The Board")

click = None
with st.container(key="board_frame"):
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
        st.warning("Install `streamlit-image-coordinates` for click-to-draw: `pip install -r requirements.txt`")

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
                st.session_state.draw_state.angles[idx] = _random_angle_for_slot(spread, idx, deck)
            else:
                # Click on existing card => FLIP
                st.session_state.draw_state.angles[idx] = _toggle_angle(
                    spread, idx, int(st.session_state.draw_state.angles[idx]), deck
                )
            # Mark click as processed BEFORE rerun to prevent flip loops
            st.session_state.last_click_unix_time[board_key] = float(click_time)
            _set_query_state(_encode_state(st.session_state.draw_state))
            _sync_host_room_if_any()
            st.rerun()
    # If click didn't hit any slot, still mark processed to avoid repeated attempts
    st.session_state.last_click_unix_time[board_key] = float(click_time)

download_bytes, download_meta = _download_png_bytes(png_bytes, spread.id, deck.id)
st.caption(f"Download optimized: {download_meta}")
st.download_button(
    "Download Board (PNG)",
    data=download_bytes,
    file_name=f"{_timestamp_slug('tarozon-spread')}-{spread.id}-{deck.id}.png",
    mime="image/png",
    use_container_width=True,
    key=f"download_board_{spread.id}_{deck.id}",
)

# host_room_code 있을 때만 채팅 fragment 호출(3초 주기). 없으면 호출 안 함.
if current_room_code:
    _fragment_host_chat(current_room_code)

st.markdown("---")
st.subheader("Grand Interpretation")
st.text_area(
    "Your Question",
    key="question",
    placeholder="e.g. What guidance does today hold? / I wish to know their heart.",
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
    # Only 180° is treated as "reversed" for prompt purposes; reversible=False 덱은 항상 정방향
    flags.append(False if not deck.reversible else (int(st.session_state.draw_state.angles[i] or 0) % 360 == 180))

if ready:
    prompt_text = build_prompt_cards_with_labels(
        question=st.session_state.question,
        deck=deck,
        spread=spread,
        cards=cards,
        reversed_flags=flags,
    )
# st.info 제거: 등장/퇴장 시 레이아웃 점프 원인이었음. 안내는 text_area placeholder로 대체.

st.markdown("**Grand Interpretation**")
st.text_area(
    "Grand Interpretation",
    value=prompt_text,
    height=240,
    label_visibility="collapsed",
    placeholder="Fill all slots to generate your Grand Interpretation.",
)
# 복사 버튼: text_area 아래 한 줄 고정. HTML/JS로 클립보드 처리해 레이아웃 변동 없음.
_copy_html = f"""<script>var __copyText={json.dumps(prompt_text)};</script><button onclick="navigator.clipboard.writeText(__copyText);this.textContent='Copied.';setTimeout(function(){{this.textContent='Copy';}}.bind(this),1200);" style="padding:0.4em 0.8em;cursor:pointer;border:2px solid #D4AF37;border-radius:0;background:#A2231D;color:white;">Copy</button>"""
components.html(_copy_html, height=40)

with st.container():
    col1, col2, col3 = st.columns(3)
    with col1:
        st.link_button("ChatGPT", url="https://chatgpt.com", use_container_width=True)
    with col2:
        st.link_button("Gemini", url="https://gemini.google.com", use_container_width=True)
    with col3:
        st.link_button("Grok", url="https://x.com/i/grok", use_container_width=True)
    st.caption("Copy the prompt and paste into your preferred AI channel.")

# URL은 "사용자 액션(클릭/DRAW ALL/리셋/선택 변경)"에서만 업데이트합니다.