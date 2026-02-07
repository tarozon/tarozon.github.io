from __future__ import annotations

from .decks import Card, Deck
from .spreads import Spread


def build_prompt_cards_with_labels(
    *,
    question: str,
    deck: Deck,
    spread: Spread,
    cards: list[Card],
    reversed_flags: list[bool],
) -> str:
    q = (question or "").strip() or "(질문 내용 없음)"

    lines: list[str] = []
    for slot, card, rev in zip(spread.slots, cards, reversed_flags, strict=False):
        orientation = "역방향" if rev else "정방향"
        lines.append(f"- {slot.label}: {card.display_name} ({orientation})")

    deck_line = f"{spread.prompt.deck_line_prefix} {deck.name}이고, {spread.prompt.cards_intro}"

    return (
        "내 질문은 다음과 같아:\n"
        f"{q}\n\n"
        f"{deck_line}\n"
        + "\n".join(lines)
        + "\n\n"
        "이 카드들을 바탕으로 내 질문에 대한 답변을 해주고, 카드에 대한 조언도 함께 해줘."
    )


def build_prompt_1card(*, question: str, deck: Deck, card: Card, reversed_: bool) -> str:
    # Backwards-compatible helper for existing callers
    from .spreads import SpreadSlot, PromptSpec

    spread = Spread(
        id="one_card_inline",
        name="1카드",
        slots=(SpreadSlot(key="card1", label="1카드"),),
        prompt=PromptSpec(type="cards_with_labels", deck_line_prefix="사용한 덱은", cards_intro="뽑은 카드는 아래와 같아."),
    )
    return build_prompt_cards_with_labels(
        question=question,
        deck=deck,
        spread=spread,
        cards=[card],
        reversed_flags=[reversed_],
    )

