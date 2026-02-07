from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SpreadSlot:
    key: str
    label: str
    allowed_angles: tuple[int, ...] = (0, 180)


@dataclass(frozen=True)
class PromptSpec:
    type: str
    deck_line_prefix: str
    cards_intro: str


@dataclass(frozen=True)
class LayoutSlot:
    key: str
    anchor: str  # "center" or "topleft"
    cx: float | None
    cy: float | None
    x: float | None
    y: float | None
    z: int
    allowed_angles: tuple[int, ...]


@dataclass(frozen=True)
class CanvasSpec:
    width: int
    height: int
    background: str  # hex color, e.g. "#fffdf2"


@dataclass(frozen=True)
class CardSpec:
    width: int
    height: int


@dataclass(frozen=True)
class LayoutSpec:
    type: str  # "absolute"
    scale: float
    canvas: CanvasSpec
    card: CardSpec
    slots: tuple[LayoutSlot, ...]

    def slot_by_key(self, key: str) -> LayoutSlot | None:
        for s in self.slots:
            if s.key == key:
                return s
        return None


@dataclass(frozen=True)
class Spread:
    id: str
    name: str
    slots: tuple[SpreadSlot, ...]
    prompt: PromptSpec
    layout: LayoutSpec | None = None

    @property
    def n_cards(self) -> int:
        return len(self.slots)


def _load_spread_json(path: Path) -> Spread:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    spread_id = str(raw["id"])
    spread_name = str(raw.get("name", spread_id))

    slots: list[SpreadSlot] = []
    for s in raw.get("slots", []):
        allowed_angles = s.get("allowed_angles")
        if isinstance(allowed_angles, list) and all(isinstance(a, int) for a in allowed_angles) and allowed_angles:
            allowed = tuple(int(a) for a in allowed_angles)
        else:
            allowed = (0, 180)
        slots.append(SpreadSlot(key=str(s["key"]), label=str(s["label"]), allowed_angles=allowed))

    prompt_raw = raw.get("prompt") or {}
    prompt = PromptSpec(
        type=str(prompt_raw.get("type", "cards_with_labels")),
        deck_line_prefix=str(prompt_raw.get("deck_line_prefix", "사용한 덱은")),
        cards_intro=str(prompt_raw.get("cards_intro", "뽑은 카드는 아래와 같아.")),
    )

    layout: LayoutSpec | None = None
    layout_raw = raw.get("layout")
    if isinstance(layout_raw, dict):
        canvas_raw = layout_raw.get("canvas") or {}
        card_raw = layout_raw.get("card") or {}
        canvas = CanvasSpec(
            width=int(canvas_raw.get("width", 1200)),
            height=int(canvas_raw.get("height", 800)),
            background=str(canvas_raw.get("background", "#fffdf2")),
        )
        card = CardSpec(width=int(card_raw.get("width", 144)), height=int(card_raw.get("height", 252)))
        scale = float(layout_raw.get("scale", 1.0))
        layout_slots: list[LayoutSlot] = []
        for ls in layout_raw.get("slots", []) or []:
            allowed_angles = ls.get("allowed_angles")
            if isinstance(allowed_angles, list) and all(isinstance(a, int) for a in allowed_angles) and allowed_angles:
                allowed = tuple(int(a) for a in allowed_angles)
            else:
                allowed = (0, 180)
            layout_slots.append(
                LayoutSlot(
                    key=str(ls["key"]),
                    anchor=str(ls.get("anchor", "center")),
                    cx=float(ls["cx"]) if "cx" in ls else None,
                    cy=float(ls["cy"]) if "cy" in ls else None,
                    x=float(ls["x"]) if "x" in ls else None,
                    y=float(ls["y"]) if "y" in ls else None,
                    z=int(ls.get("z", 0)),
                    allowed_angles=allowed,
                )
            )
        layout = LayoutSpec(
            type=str(layout_raw.get("type", "absolute")),
            scale=scale,
            canvas=canvas,
            card=card,
            slots=tuple(layout_slots),
        )

    return Spread(id=spread_id, name=spread_name, slots=tuple(slots), prompt=prompt, layout=layout)


def load_spreads(repo_root: Path) -> dict[str, Spread]:
    spreads_dir = repo_root / "data" / "spreads"
    spreads: dict[str, Spread] = {}
    if not spreads_dir.exists():
        return spreads
    for p in sorted(spreads_dir.glob("*.json")):
        spread = _load_spread_json(p)
        spreads[spread.id] = spread
    return spreads

