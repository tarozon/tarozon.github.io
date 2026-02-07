from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from tarozon_core.decks import Deck, load_decks
from tarozon_core.draw import draw_one, random_reversed
from tarozon_core.images import card_image_bytes
from tarozon_core.prompts import build_prompt_1card


@dataclass
class DrawState:
    deck_id: str
    code: str | None = None
    reversed_: bool = False


REPO_ROOT = Path(__file__).resolve().parent


def _ensure_state(default_deck_id: str) -> None:
    if "draw_state" not in st.session_state:
        st.session_state.draw_state = DrawState(deck_id=default_deck_id)
    if "question" not in st.session_state:
        st.session_state.question = ""


def _timestamp_slug(prefix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}"


st.set_page_config(
    page_title="TAROZON",
    page_icon="🔮",
    layout="centered",
)

decks = load_decks(REPO_ROOT)
if not decks:
    st.error("덱 데이터를 찾을 수 없어요. `data/decks/*.json` 확인이 필요합니다.")
    st.stop()

default_deck_id = "rws" if "rws" in decks else sorted(decks.keys())[0]
_ensure_state(default_deck_id=default_deck_id)

st.title("🔮 TAROZON (Streamlit MVP)")
st.caption("덱 선택 → 1카드 DRAW/FLIP → 리딩 요청문 생성/다운로드 → 이미지 다운로드")

with st.sidebar:
    st.header("설정")
    deck_options = {d.name: d.id for d in decks.values()}
    selected_name = st.selectbox(
        "덱 선택",
        options=sorted(deck_options.keys()),
        index=sorted(deck_options.keys()).index(
            next(name for name, did in deck_options.items() if did == st.session_state.draw_state.deck_id)
        )
        if st.session_state.draw_state.deck_id in deck_options.values()
        else 0,
    )
    selected_deck_id = deck_options[selected_name]
    if selected_deck_id != st.session_state.draw_state.deck_id:
        st.session_state.draw_state = DrawState(deck_id=selected_deck_id)

    st.divider()
    if st.button("🧹 New Spread (리셋)", use_container_width=True):
        st.session_state.draw_state = DrawState(deck_id=selected_deck_id)
        st.session_state.question = ""
        st.rerun()

deck: Deck = decks[st.session_state.draw_state.deck_id]

st.subheader("🃏 1카드 스프레드")

col_a, col_b, col_c = st.columns([1, 1, 1])

with col_a:
    if st.button("DRAW", use_container_width=True, type="primary"):
        code = draw_one(deck)
        st.session_state.draw_state.code = code
        st.session_state.draw_state.reversed_ = random_reversed()
        st.rerun()

with col_b:
    can_flip = st.session_state.draw_state.code is not None
    if st.button("FLIP (정/역)", use_container_width=True, disabled=not can_flip):
        st.session_state.draw_state.reversed_ = not st.session_state.draw_state.reversed_
        st.rerun()

with col_c:
    if st.button("카드 지우기", use_container_width=True, disabled=st.session_state.draw_state.code is None):
        st.session_state.draw_state.code = None
        st.session_state.draw_state.reversed_ = False
        st.rerun()

st.markdown("---")

if st.session_state.draw_state.code is None:
    st.info("왼쪽에서 덱을 고른 뒤, `DRAW`를 눌러 카드를 뽑아주세요.")
else:
    code = st.session_state.draw_state.code
    card = deck.card_by_code(code)
    if card is None:
        st.error(f"카드 코드를 찾을 수 없어요: {code}")
        st.stop()

    orientation = "역방향" if st.session_state.draw_state.reversed_ else "정방향"
    st.write(f"**선택된 카드**: {card.display_name}  ·  **{orientation}**")

    try:
        jpg = card_image_bytes(deck, REPO_ROOT, code=code, reversed_=st.session_state.draw_state.reversed_)
        st.image(jpg, use_container_width=True)
        st.download_button(
            "🖼️ 카드 이미지 다운로드(JPG)",
            data=jpg,
            file_name=f"{_timestamp_slug('tarozon-card')}-{deck.id}-{code}.jpg",
            mime="image/jpeg",
            use_container_width=True,
        )
    except FileNotFoundError as e:
        st.warning(str(e))

st.subheader("📝 GPT 리딩 요청문")
st.text_area(
    "질문 입력",
    key="question",
    placeholder="예: 오늘의 조언을 알려줘. / 상대방의 마음이 궁금해.",
    height=120,
)

prompt_text = ""
if st.session_state.draw_state.code is not None:
    card = deck.card_by_code(st.session_state.draw_state.code)
    if card:
        prompt_text = build_prompt_1card(
            question=st.session_state.question,
            deck=deck,
            card=card,
            reversed_=st.session_state.draw_state.reversed_,
        )

st.text_area("생성된 요청문", value=prompt_text, height=220)

st.download_button(
    "📄 요청문 다운로드(TXT)",
    data=(prompt_text or "").encode("utf-8"),
    file_name=f"{_timestamp_slug('tarozon-prompt')}.txt",
    mime="text/plain; charset=utf-8",
    use_container_width=True,
    disabled=not bool(prompt_text.strip()),
)