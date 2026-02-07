from __future__ import annotations

import random
from collections.abc import Iterable

from .decks import Deck


def draw_one(deck: Deck, exclude_codes: Iterable[str] = ()) -> str:
    exclude = set(exclude_codes)
    available = [c.code for c in deck.cards if c.code not in exclude]
    if not available:
        raise ValueError("No available cards to draw.")
    return random.choice(available)


def draw_many(deck: Deck, n: int, exclude_codes: Iterable[str] = ()) -> list[str]:
    exclude = set(exclude_codes)
    available = [c.code for c in deck.cards if c.code not in exclude]
    if n < 0:
        raise ValueError("n must be >= 0")
    if len(available) < n:
        raise ValueError("Not enough available cards to draw.")
    return random.sample(available, k=n)


def random_reversed() -> bool:
    return random.random() > 0.5

