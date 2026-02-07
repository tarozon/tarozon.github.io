"""Supabase rooms table manager for real-time reading exchange."""

from __future__ import annotations

import os
import random
import string
from datetime import datetime, timezone
from typing import Any

_ROOM_CODE_LENGTH = 6
_MAX_CREATE_ATTEMPTS = 10


def _generate_room_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(_ROOM_CODE_LENGTH))


class RoomManager:
    """Manages rooms table in Supabase for sharing draw state."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self._url = url or os.environ.get("SUPABASE_URL", "").strip()
        self._key = key or os.environ.get("SUPABASE_SERVICE_KEY", "").strip() or os.environ.get("SUPABASE_ANON_KEY", "").strip()
        self._client = None
        if self._url and self._key:
            try:
                from supabase import create_client
                self._client = create_client(self._url, self._key)
            except Exception:
                self._client = None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def create_room(self, state: dict[str, Any]) -> str | None:
        """Create a room with current state_json. Returns 6-char room_code or None on failure."""
        if not self._client:
            return None
        for _ in range(_MAX_CREATE_ATTEMPTS):
            code = _generate_room_code()
            try:
                self._client.table("rooms").insert({
                    "room_code": code,
                    "state_json": state,
                }).execute()
                return code
            except Exception:
                continue
        return None

    def get_room(self, room_code: str) -> dict[str, Any] | None:
        """Fetch room by room_code. Returns dict with state_json, updated_at, etc. or None."""
        if not self._client or not (room_code and room_code.strip()):
            return None
        code = room_code.strip().upper()
        try:
            r = self._client.table("rooms").select("state_json, updated_at").eq("room_code", code).limit(1).execute()
            if r.data and len(r.data) > 0:
                row = r.data[0]
                return {
                    "state_json": row.get("state_json"),
                    "updated_at": row.get("updated_at"),
                }
        except Exception:
            pass
        return None

    def update_room(self, room_code: str, state: dict[str, Any]) -> bool:
        """Update room's state_json and updated_at. Returns True on success."""
        if not self._client or not (room_code and room_code.strip()):
            return False
        code = room_code.strip().upper()
        try:
            self._client.table("rooms").update({
                "state_json": state,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("room_code", code).execute()
            return True
        except Exception:
            return False


class ChatManager:
    """Manages messages table in Supabase for real-time chat in a room."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        self._url = url or os.environ.get("SUPABASE_URL", "").strip()
        self._key = key or os.environ.get("SUPABASE_SERVICE_KEY", "").strip() or os.environ.get("SUPABASE_ANON_KEY", "").strip()
        self._client = None
        if self._url and self._key:
            try:
                from supabase import create_client
                self._client = create_client(self._url, self._key)
            except Exception:
                self._client = None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def send_message(self, room_code: str, user_name: str, content: str) -> bool:
        """Insert a message into the messages table. Returns True on success."""
        if not self._client or not (room_code and room_code.strip()) or not content or not content.strip():
            return False
        code = room_code.strip().upper()
        try:
            self._client.table("messages").insert({
                "room_code": code,
                "user_name": (user_name or "알 수 없음").strip() or "알 수 없음",
                "content": content.strip(),
            }).execute()
            return True
        except Exception:
            return False

    def get_messages(self, room_code: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent messages for the room, oldest first. Returns list of dicts with user_name, content, created_at."""
        if not self._client or not (room_code and room_code.strip()):
            return []
        code = room_code.strip().upper()
        try:
            r = (
                self._client.table("messages")
                .select("user_name, content, created_at")
                .eq("room_code", code)
                .order("created_at")
                .limit(limit)
                .execute()
            )
            if not r.data:
                return []
            return [
                {"user_name": row.get("user_name", ""), "content": row.get("content", ""), "created_at": row.get("created_at", "")}
                for row in r.data
            ]
        except Exception:
            return []
