from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Card:
    code: str
    name: str
    extra: dict[str, Any]

    @property
    def display_name(self) -> str:
        # Prefer explicit "label" if present; otherwise derive from known keys.
        label = self.extra.get("label")
        if isinstance(label, str) and label.strip():
            return label

        korean = self.extra.get("korean")
        hanja = self.extra.get("hanja")
        if isinstance(korean, str) and korean.strip():
            if isinstance(hanja, str) and hanja.strip():
                return f"{self.code}. {korean} ({hanja})"
            return f"{self.code}. {korean}"

        return f"{self.code} {self.name}".strip()


@dataclass(frozen=True)
class Deck:
    id: str
    name: str
    image_dir: str
    back_image: str | None
    cards: tuple[Card, ...]
    reversible: bool = True

    def card_by_code(self, code: str) -> Card | None:
        for c in self.cards:
            if c.code == code:
                return c
        return None


def _load_deck_json(path: Path) -> Deck:
    raw = json.loads(path.read_text(encoding="utf-8"))
    deck_id = str(raw["id"])
    deck_name = str(raw.get("name", deck_id))
    image_dir = str(raw["image_dir"])
    back_image = raw.get("back_image")
    back_image = str(back_image) if back_image else None
    reversible = bool(raw.get("reversible", True))

    cards: list[Card] = []
    for item in raw.get("cards", []):
        code = str(item["code"])
        name = str(item.get("name", "")).strip()
        extra = {k: v for k, v in item.items() if k not in {"code", "name"}}
        cards.append(Card(code=code, name=name, extra=extra))

    return Deck(
        id=deck_id,
        name=deck_name,
        image_dir=image_dir,
        back_image=back_image,
        cards=tuple(cards),
        reversible=reversible,
    )


def load_decks(repo_root: Path) -> dict[str, Deck]:
    decks_dir = repo_root / "data" / "decks"
    decks: dict[str, Deck] = {}
    for p in sorted(decks_dir.glob("*.json")):
        deck = _load_deck_json(p)
        decks[deck.id] = deck
    return decks

