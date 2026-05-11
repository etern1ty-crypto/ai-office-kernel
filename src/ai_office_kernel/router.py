from __future__ import annotations

import re
from dataclasses import dataclass

from ai_office_kernel.roles import DEFAULT_ROLES, RoleId


@dataclass(frozen=True)
class RouteDecision:
    role_id: RoleId
    task_text: str
    reason: str
    run_qa: bool = False


class Router:
    _code_keywords = {
        "code",
        "script",
        "parser",
        "refactor",
        "bug",
        "test",
        "tests",
        "python",
        "javascript",
        "typescript",
        "api",
        "cli",
        "код",
        "скрипт",
        "парсер",
        "рефактор",
        "ошибка",
        "тест",
        "тесты",
        "файл",
    }
    _qa_keywords = {
        "review",
        "audit",
        "security",
        "vulnerability",
        "проверь",
        "ревью",
        "аудит",
        "уязвимость",
        "безопасность",
    }

    def __init__(self, roles=DEFAULT_ROLES):
        self._roles = roles

    def route(self, text: str) -> RouteDecision:
        normalized = text.strip()
        lower = normalized.lower()

        for role_id, role in self._roles.items():
            for trigger in role.triggers:
                if lower == trigger or lower.startswith(trigger + " "):
                    return RouteDecision(
                        role_id=role_id,
                        task_text=normalized[len(trigger) :].strip() or normalized,
                        reason=f"explicit trigger {trigger}",
                        run_qa=role_id == "developer",
                    )

        tokens = set(re.findall(r"[\w.-]+", lower, flags=re.UNICODE))
        if tokens & self._qa_keywords:
            return RouteDecision(
                role_id="qa",
                task_text=normalized,
                reason="qa keyword",
                run_qa=False,
            )
        if tokens & self._code_keywords:
            return RouteDecision(
                role_id="developer",
                task_text=normalized,
                reason="code keyword",
                run_qa=True,
            )
        return RouteDecision(
            role_id="secretary",
            task_text=normalized,
            reason="default secretary route",
            run_qa=False,
        )

