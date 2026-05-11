from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Deque


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    user_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SharedMemory:
    def __init__(self, max_messages: int = 30):
        if max_messages < 1:
            raise ValueError("max_messages must be positive")
        self._max_messages = max_messages
        self._messages: dict[int, Deque[ChatMessage]] = defaultdict(
            lambda: deque(maxlen=self._max_messages)
        )

    def add(self, chat_id: int, message: ChatMessage) -> None:
        self._messages[chat_id].append(message)

    def recent(self, chat_id: int, limit: int | None = None) -> list[ChatMessage]:
        messages = list(self._messages[chat_id])
        if limit is None:
            return messages
        return messages[-limit:]

    def context_text(self, chat_id: int, limit: int | None = None) -> str:
        lines: list[str] = []
        for message in self.recent(chat_id, limit):
            lines.append(f"{message.role}: {message.content}")
        return "\n".join(lines)

