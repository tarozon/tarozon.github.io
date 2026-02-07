from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import streamlit as st

from tarozon_core.decks import Deck, load_decks
from tarozon_core.draw import draw_many, draw_one, random_reversed
from tarozon_core.images import card_image_bytes, load_static_image_bytes
from tarozon_core.prompts import build_prompt_cards_with_labels
from tarozon_core.spreads import Spread, load_spreads


@dataclass
class DrawState:
    deck_id: str
    spread_id: str
    codes: list[str | None]
    reversed_flags: list[bool]


REPO_ROOT = Path(__file__).resolve().parent


def _ensure_state(default_deck_id: str, default_spread_id: str, default_n: int) -> None:
    if "draw_state" not in st.session_state:
        st.session_state.draw_state = DrawState(
            deck_id=default_deck_id,
            spread_id=default_spread_id,
            codes=[None for _ in range(default_n)],
            reversed_flags=[False for _ in range(default_n)],
        )
    if "question" not in st.session_state:
        st.session_state.question = ""
    if "manual_mode" not in st.session_state:
        st.session_state.manual_mode = False


def _timestamp_slug(prefix: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}"


st.set_page_config(page_title="TAROZON", page_icon="🔮", layout="centered")

decks = load_decks(REPO_ROOT)
if not decks:
    st.error("덱 데이터를 찾을 수 없어요. `data/decks/*.json` 확인이 필요합니다.")
    st.stop()

spreads = load_spreads(REPO_ROOT)
if not spreads:
    st.error("스프레드 데이터를 찾을 수 없어요. `data/spreads/*.json` 확인이 필요합니다.")
    st.stop()

default_deck_id = "rws" if "rws" in decks else sorted(decks.keys())[0]
default_spread_id = "one_card" if "one_card" in spreads else sorted(spreads.keys())[0]
_ensure_state(
    default_deck_id=default_deck_id,
    default_spread_id=default_spread_id,
    default_n=spreads[default_spread_id].n_cards,
)

deck: Deck = decks[st.session_state.draw_state.deck_id]
spread: Spread = spreads[st.session_state.draw_state.spread_id]

# Normalize state length (in case JSON changed)
if len(st.session_state.draw_state.codes) != spread.n_cards:
    st.session_state.draw_state.codes = [None for _ in range(spread.n_cards)]
    st.session_state.draw_state.reversed_flags = [False for _ in range(spread.n_cards)]

st.title("🔮 TAROZON (Streamlit MVP)")
st.caption("덱 선택 → 스프레드 선택 → DRAW/FLIP → 리딩 요청문 생성/다운로드 → 이미지 다운로드")

with st.sidebar:
    st.header("설정")

    deck_options = {d.name: d.id for d in decks.values()}
    spread_options = {s.name: s.id for s in spreads.values()}

    selected_deck_name = st.selectbox(
        "덱 선택",
        options=sorted(deck_options.keys()),
        index=sorted(deck_options.keys()).index(next(n for n, did in deck_options.items() if did == deck.id)),
    )
    selected_spread_name = st.selectbox(
        "스프레드 선택",
        options=sorted(spread_options.keys()),
        index=sorted(spread_options.keys()).index(next(n for n, sid in spread_options.items() if sid == spread.id)),
    )

    st.session_state.manual_mode = st.toggle("수동 선택(드롭다운)", value=st.session_state.manual_mode)

    st.divider()
    if st.button("🧹 New Spread (리셋)", use_container_width=True):
        st.session_state.draw_state = DrawState(
            deck_id=deck.id,
            spread_id=spread.id,
            codes=[None for _ in range(spread.n_cards)],
            reversed_flags=[False for _ in range(spread.n_cards)],
        )
        st.session_state.question = ""
        st.rerun()

    new_deck_id = deck_options[selected_deck_name]
    new_spread_id = spread_options[selected_spread_name]
    if new_deck_id != deck.id or new_spread_id != spread.id:
        new_spread = spreads[new_spread_id]
        st.session_state.draw_state = DrawState(
            deck_id=new_deck_id,
            spread_id=new_spread_id,
            codes=[None for _ in range(new_spread.n_cards)],
            reversed_flags=[False for _ in range(new_spread.n_cards)],
        )
        st.session_state.question = ""
        st.rerun()

deck = decks[st.session_state.draw_state.deck_id]
spread = spreads[st.session_state.draw_state.spread_id]

st.subheader(f"🃏 {spread.name}")

top_a, top_b = st.columns([2, 1])
with top_a:
    if st.button("DRAW ALL", use_container_width=True, type="primary"):
        existing = [c for c in st.session_state.draw_state.codes if c]
        need = sum(1 for c in st.session_state.draw_state.codes if c is None)
        new_codes = draw_many(deck, need, exclude_codes=existing) if need else []
        it = iter(new_codes)
        for i in range(spread.n_cards):
            if st.session_state.draw_state.codes[i] is None:
                st.session_state.draw_state.codes[i] = next(it)
                st.session_state.draw_state.reversed_flags[i] = random_reversed()
        st.rerun()

with top_b:
    if st.button("전체 지우기", use_container_width=True):
        st.session_state.draw_state.codes = [None for _ in range(spread.n_cards)]
        st.session_state.draw_state.reversed_flags = [False for _ in range(spread.n_cards)]
        st.rerun()

st.markdown("---")

card_label_to_code = {c.display_name: c.code for c in deck.cards}
manual_labels = ["(랜덤/미선택)"] + [c.display_name for c in deck.cards]

cols = st.columns(spread.n_cards)
for i, slot in enumerate(spread.slots):
    with cols[i]:
        st.markdown(f"**{slot.label}**")

        if st.session_state.manual_mode:
            current_code = st.session_state.draw_state.codes[i]
            current_label = "(랜덤/미선택)"
            if current_code:
                card_obj = deck.card_by_code(current_code)
                if card_obj:
                    current_label = card_obj.display_name

            chosen = st.selectbox(
                "카드 선택",
                options=manual_labels,
                index=manual_labels.index(current_label) if current_label in manual_labels else 0,
                key=f"manual_select_{spread.id}_{deck.id}_{i}",
                label_visibility="collapsed",
            )

            if chosen == "(랜덤/미선택)":
                if st.session_state.draw_state.codes[i] is not None:
                    st.session_state.draw_state.codes[i] = None
                    st.session_state.draw_state.reversed_flags[i] = False
                    st.rerun()
            else:
                new_code = card_label_to_code[chosen]
                if st.session_state.draw_state.codes[i] != new_code:
                    st.session_state.draw_state.codes[i] = new_code
                    if current_code is None:
                        st.session_state.draw_state.reversed_flags[i] = random_reversed()
                    st.rerun()

        btn1, btn2, btn3 = st.columns([1, 1, 1])
        with btn1:
            if st.button("DRAW", key=f"draw_{i}", use_container_width=True):
                used = [c for j, c in enumerate(st.session_state.draw_state.codes) if c and j != i]
                st.session_state.draw_state.codes[i] = draw_one(deck, exclude_codes=used)
                st.session_state.draw_state.reversed_flags[i] = random_reversed()
                st.rerun()
        with btn2:
            if st.button(
                "FLIP",
                key=f"flip_{i}",
                use_container_width=True,
                disabled=st.session_state.draw_state.codes[i] is None,
            ):
                st.session_state.draw_state.reversed_flags[i] = not st.session_state.draw_state.reversed_flags[i]
                st.rerun()
        with btn3:
            if st.button(
                "지우기",
                key=f"clear_{i}",
                use_container_width=True,
                disabled=st.session_state.draw_state.codes[i] is None,
            ):
                st.session_state.draw_state.codes[i] = None
                st.session_state.draw_state.reversed_flags[i] = False
                st.rerun()

        code = st.session_state.draw_state.codes[i]
        if code is None:
            if deck.back_image:
                try:
                    back_jpg = load_static_image_bytes(REPO_ROOT, deck.back_image)
                    st.image(back_jpg, use_container_width=True)
                except FileNotFoundError:
                    st.info("카드를 뽑아주세요.")
            else:
                st.info("카드를 뽑아주세요.")
        else:
            card = deck.card_by_code(code)
            if card is None:
                st.error(f"카드 코드를 찾을 수 없어요: {code}")
                st.stop()
            orientation = "역방향" if st.session_state.draw_state.reversed_flags[i] else "정방향"
            st.caption(f"{card.display_name} · {orientation}")
            try:
                jpg = card_image_bytes(deck, REPO_ROOT, code=code, reversed_=st.session_state.draw_state.reversed_flags[i])
                st.image(jpg, use_container_width=True)
                st.download_button(
                    "🖼️ 이미지 다운로드",
                    data=jpg,
                    file_name=f"{_timestamp_slug('tarozon-card')}-{deck.id}-{code}-{slot.key}.jpg",
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
    flags.append(st.session_state.draw_state.reversed_flags[i])

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