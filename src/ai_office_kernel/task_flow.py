from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TaskBackend = Literal["local", "gemini"]


@dataclass
class TaskSession:
    description: str
    gemini_model: str
    backend: TaskBackend | None = None
    notes: list[str] = field(default_factory=list)
    pending_model: str | None = None
    pending_run: bool = False

    def add_note(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.notes.append(text)
        if len(self.notes) > 20:
            self.notes = self.notes[-20:]
        self.pending_run = False

    def set_backend(self, backend: TaskBackend) -> None:
        self.backend = backend
        self.pending_run = False

    def request_model_change(self, model: str) -> None:
        self.pending_model = model.strip()
        self.pending_run = False

    def confirm_model_change(self) -> str | None:
        if not self.pending_model:
            return None
        self.gemini_model = self.pending_model
        self.pending_model = None
        self.pending_run = False
        return self.gemini_model

    def request_run(self) -> None:
        self.pending_run = True

    def consume_run_confirmation(self) -> bool:
        if not self.pending_run:
            return False
        self.pending_run = False
        return True

    def full_prompt(self) -> str:
        parts = [f"Задача:\n{self.description.strip()}"]
        if self.notes:
            parts.append("Уточнения:\n" + "\n".join(f"- {note}" for note in self.notes))
        return "\n\n".join(parts)

    def execution_prompt(self) -> str:
        return (
            "Ниже сырой диалог с Никитой. Он может писать неформально; твоя задача - "
            "превратить это в понятное ТЗ и выполнить разработку.\n\n"
            "Сначала восстанови краткий technical brief: цель, стек, функции, дизайн, "
            "источники данных, ограничения безопасности, файлы, команды запуска и acceptance checks. "
            "Затем создай или измени файлы проекта через доступные file tools. "
            "Не утверждай, что запускал npm/dev-server/тесты, если CLI tools реально не выполняли команды.\n\n"
            f"{self.full_prompt()}"
        )

    def status_text(self) -> str:
        backend = self.backend or "не выбран"
        note_count = len(self.notes)
        pending = ""
        if self.pending_model:
            pending = f"\nОжидает подтверждения модель: {self.pending_model}"
        elif self.pending_run:
            pending = "\nОжидает подтверждения запуск: /confirm"
        return (
            "Активная задача:\n"
            f"{self.description}\n\n"
            f"Backend: {backend}\n"
            f"Gemini model: {self.gemini_model}\n"
            f"Уточнений: {note_count}"
            f"{pending}"
        )
