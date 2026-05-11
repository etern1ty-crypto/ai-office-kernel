from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ai_office_kernel.config import Settings
from ai_office_kernel.local import OllamaClient, OllamaError
from ai_office_kernel.memory import ChatMessage, SharedMemory
from ai_office_kernel.roles import role_prompt
from ai_office_kernel.tools import ToolRequest, ToolRuntime, auto_execute_risk


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    label: str
    content: str = ""
    tool: str | None = None
    risk: str | None = None


@dataclass
class AgentPendingConfirmation:
    chat_id: int
    messages: list[dict[str, Any]]
    request: ToolRequest


@dataclass
class AgentRunResult:
    text: str
    events: list[AgentEvent] = field(default_factory=list)
    status: str = "done"
    pending: AgentPendingConfirmation | None = None


ToolRuntimeFactory = Callable[[int], ToolRuntime]


class SecretaryAgentLoop:
    def __init__(
        self,
        settings: Settings,
        *,
        ollama: OllamaClient,
        memory: SharedMemory,
        tool_runtime_factory: ToolRuntimeFactory,
        max_rounds: int = 8,
    ):
        self.settings = settings
        self.ollama = ollama
        self.memory = memory
        self.tool_runtime_factory = tool_runtime_factory
        self.max_rounds = max_rounds

    def run(self, chat_id: int, user_id: int | None, text: str, on_status: Callable[[str], None] | None = None) -> AgentRunResult:
        self.memory.add(chat_id, ChatMessage(role="user", content=text, user_id=user_id))
        runtime = self.tool_runtime_factory(chat_id)
        tools_allowed = _should_enable_tools(text)
        messages = self._initial_messages(chat_id, text, tools_allowed=tools_allowed)
        result = self._run_messages(chat_id, messages, runtime, tools_allowed=tools_allowed, on_status=on_status)
        self.memory.add(chat_id, ChatMessage(role="Secretary", content=result.text))
        return result

    def resume_confirmed(self, pending: AgentPendingConfirmation, on_status: Callable[[str], None] | None = None) -> AgentRunResult:
        runtime = self.tool_runtime_factory(pending.chat_id)
        events = [
            AgentEvent(
                kind="tool_started",
                label=f"Выполняю подтвержденное действие: {pending.request.name}",
                tool=pending.request.name,
                risk=pending.request.risk,
            )
        ]
        execution = runtime.execute(pending.request)
        events.append(
            AgentEvent(
                kind="tool_result",
                label=f"Готово: {pending.request.name}" if execution.ok else f"Ошибка: {pending.request.name}",
                content=execution.content,
                tool=pending.request.name,
                risk=pending.request.risk,
            )
        )
        messages = [
            *pending.messages,
            {"role": "tool", "name": pending.request.name, "content": execution.as_tool_message()},
        ]
        result = self._run_messages(pending.chat_id, messages, runtime, tools_allowed=True, on_status=on_status)
        result.events = [*events, *result.events]
        return result

    def _initial_messages(self, chat_id: int, text: str, *, tools_allowed: bool) -> list[dict[str, Any]]:
        context = self.memory.context_text(chat_id, limit=12)
        system = role_prompt("secretary", self.settings.prompt_dir) + "\n\n" + AGENT_PROTOCOL_PROMPT
        if not tools_allowed:
            system += "\n\n" + CHAT_ONLY_PROMPT
            return [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ]
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Workspace root: {self.settings.workspace_root}\n"
                    "Workspace alias: /workspace means the configured workspace root. "
                    "If you need to inspect projects, start with workspace_info or list_dir path='.'.\n\n"
                    f"Recent context:\n{context}\n\nUser message:\n{text}"
                ),
            },
        ]

    def _run_messages(
        self,
        chat_id: int,
        messages: list[dict[str, Any]],
        runtime: ToolRuntime,
        *,
        tools_allowed: bool,
        on_status: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        events: list[AgentEvent] = []
        final_text = ""
        
        for _round in range(self.max_rounds):
            tools = runtime.tool_schemas() if tools_allowed else None
            try:
                response = self.ollama.chat(
                    messages,
                    model=self.settings.router_model,
                    tools=tools,
                    think=False,
                )
            except OllamaError as exc:
                return AgentRunResult(
                    text=f"Управляющий недоступен (Ollama error): {exc}",
                    events=events,
                    status="error",
                )

            message = response.get("message")
            if not isinstance(message, dict):
                return AgentRunResult(
                    text="Управляющий вернул некорректный ответ.",
                    events=events,
                    status="error",
                )

            content = str(message.get("content") or "").strip()
            tool_calls = _extract_tool_calls(message)
            
            # По умолчанию берем текст из контента
            current_report = content

            if tool_calls:
                if not tools_allowed:
                    return AgentRunResult(
                        text=content or "Не использую инструменты в этом режиме.",
                        events=events,
                        status="done",
                    )
                messages.append(_assistant_message_for_history(message))
                pending = self._execute_requests(chat_id, messages, runtime, tool_calls, events, on_status=on_status)
                if pending:
                    # Если нужно подтверждение, возвращаем отчет управляющего или дефолт
                    text = _extract_user_message(content) or content or f"Нужно подтверждение для: {pending.request.name}"
                    return AgentRunResult(text=text, events=events, status="need_confirm", pending=pending)
                continue

            plan = _extract_json_object(content) if tools_allowed else None
            if isinstance(plan, dict):
                current_report = str(plan.get("user_message") or current_report).strip()
                running_label = str(plan.get("running_command_label") or "").strip()
                if running_label:
                    events.append(AgentEvent(kind="status", label=running_label))
                    if on_status:
                        on_status(running_label)
                
                actions = plan.get("actions")
                if isinstance(actions, list) and actions:
                    messages.append({"role": "assistant", "content": content})
                    pending = self._execute_requests(chat_id, messages, runtime, actions, events, on_status=on_status)
                    if pending:
                        return AgentRunResult(text=current_report, events=events, status="need_confirm", pending=pending)
                    continue
                
                # Если экшенов нет и статус done - завершаем
                if plan.get("status") == "done":
                    return AgentRunResult(text=current_report, events=events, status="done")

            # Если это обычный текст без инструментов и плана
            return AgentRunResult(
                text=current_report or "Задача выполнена.",
                events=events,
                status="done",
            )

        return AgentRunResult(
            text="Я дошел до лимита внутренних шагов. Проверь статус или уточни задачу.",
            events=events,
            status="max_rounds",
        )

    def _execute_requests(
        self,
        chat_id: int,
        messages: list[dict[str, Any]],
        runtime: ToolRuntime,
        raw_requests: list[Any],
        events: list[AgentEvent],
        on_status: Callable[[str], None] | None = None,
    ) -> AgentPendingConfirmation | None:
        for raw_request in raw_requests:
            request = _request_from_raw(runtime, raw_request)
            if not request.name or request.name == "unknown":
                continue
            
            label = f"Выполняю: {request.name}"
            if request.reason:
                label += f" — {request.reason}"
            
            events.append(
                AgentEvent(
                    kind="tool_started",
                    label=label,
                    tool=request.name,
                    risk=request.risk,
                )
            )
            if on_status:
                on_status(label)

            if _should_reject_cloud_escalation(request):
                events.append(
                    AgentEvent(
                        kind="status",
                        label="Сначала проверяю проект локально, без Gemini CLI.",
                        tool=request.name,
                        risk=request.risk,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "name": request.name,
                        "content": (
                            "[TOOL ask_gemini_cli rejected]\n"
                            "This is a local GitHub publishing / .gitignore / secret-scan task. "
                            "Inspect locally first with workspace_info, list_dir, git_status, "
                            "scan_secrets, read_file/write_file. Escalate only for complex code changes."
                        ),
                    }
                )
                continue
            if not auto_execute_risk(request.risk):
                events.append(
                    AgentEvent(
                        kind="need_confirm",
                        label=f"Нужно подтверждение: {request.name}",
                        content=json.dumps(request.args, ensure_ascii=False),
                        tool=request.name,
                        risk=request.risk,
                    )
                )
                return AgentPendingConfirmation(chat_id=chat_id, messages=list(messages), request=request)

            execution = runtime.execute(request)
            events.append(
                AgentEvent(
                    kind="tool_result" if execution.ok else "tool_error",
                    label=f"Готово: {request.name}" if execution.ok else f"Ошибка: {request.name}",
                    content=execution.content,
                    tool=request.name,
                    risk=request.risk,
                )
            )
            messages.append(
                {"role": "tool", "name": request.name, "content": execution.as_tool_message()}
            )
        return None


def _request_from_raw(runtime: ToolRuntime, raw_request: Any) -> ToolRequest:
    if isinstance(raw_request, dict) and "function" in raw_request:
        function = raw_request.get("function")
        if not isinstance(function, dict):
            return runtime.request_from_action({})
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        return runtime.request_from_action(
            {
                "tool": function.get("name"),
                "args": arguments if isinstance(arguments, dict) else {},
            }
        )
    if isinstance(raw_request, dict):
        return runtime.request_from_action(raw_request)
    return runtime.request_from_action({})


def _extract_tool_calls(message: dict[str, Any]) -> list[Any]:
    calls = message.get("tool_calls")
    return calls if isinstance(calls, list) else []


def _assistant_message_for_history(message: dict[str, Any]) -> dict[str, Any]:
    history = {"role": "assistant", "content": str(message.get("content") or "")}
    if isinstance(message.get("tool_calls"), list):
        history["tool_calls"] = message["tool_calls"]
    return history


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.S)
    if fenced:
        stripped = fenced.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_user_message(text: str) -> str:
    plan = _extract_json_object(text)
    if isinstance(plan, dict) and isinstance(plan.get("user_message"), str):
        return plan["user_message"].strip()
    return ""


def _should_enable_tools(text: str) -> bool:
    lower = text.lower()
    exact_markers = (
        "ответь ровно",
        "ответь точно",
        "ровно:",
        "exactly",
        "do not use tools",
        "не используй tools",
        "не используй инструменты",
        "без инструментов",
    )
    if any(marker in lower for marker in exact_markers):
        return False

    tool_markers = (
        "давай",
        "делай",
        "продолжай",
        "запускай",
        "выполняй",
        "го",
        "go",
        "ок",
        "ok",
        "согласен",
        "подтверждаю",
        "проверь",
        "проверить",
        "посмотри",
        "прочитай",
        "найди",
        "запусти",
        "запустить",
        "создай",
        "сделай",
        "исправь",
        "почини",
        "открой",
        "скачай",
        "выгрузи",
        "отправь",
        "установи",
        "localhost",
        "локалхост",
        "сервер",
        "команд",
        "терминал",
        "ошибка",
        "traceback",
        "файл",
        "папк",
        "проект",
        "readme",
        "package.json",
        "pyproject",
        "git",
        "github",
        "gemini",
        "модель",
    )
    return any(marker in lower for marker in tool_markers)


def _should_reject_cloud_escalation(request: ToolRequest) -> bool:
    if request.name != "ask_gemini_cli":
        return False
    prompt = str(request.args.get("prompt") or "").lower()
    local_markers = (
        "github",
        "gitignore",
        ".gitignore",
        "gh repo",
        "git push",
        "git status",
        "опубли",
        "публикац",
        "репозитор",
        "секрет",
        "токен",
        "token",
        "secret",
    )
    return any(marker in prompt for marker in local_markers)


AGENT_PROTOCOL_PROMPT = """
Ты — Управляющий (Office Manager). Твоя задача — выполнить поручение Босса (Никиты).

**Твои принципы**:
1.  **Минимализм**: Не пиши лишнего. Если задача простая, ответь коротким текстом или даже эмодзи (✅, ❌, 🔥).
2.  **Форматирование**: Модели должны использовать HTML-разметку Telegram: `<code>команды</code>`, `<b>жирный</b>`, `<i>курсив</i>`. Команды и пути к файлам всегда оборачивай в code блоки.
3.  **Команда**:
    *   **Junior (ask_local_coder)**: скрипты, тесты, мелкие правки.
    *   **Senior (ask_gemini_cli)**: архитектура, тяжелый рефакторинг, новые фичи.
4.  **Файлы**: 
    *   Если Босс прислал файл, ты увидишь сообщение "Я получил файл: <name>". Ты можешь прочитать его через `read_file`.
    *   Если Босс просит "скинуть", "выгрузить" или "отправить" файл, используй `export_file`.

**Твой алгоритм**:
- Анализ -> Поиск контекста (list_dir) -> Делегирование -> Проверка -> Краткий отчет Боссу.
- Если Босс прислал файл или просит прислать — используй соответствующие инструменты.

**Формат ответа (JSON)**:
{
  "user_message": "Финальный текст или эмодзи",
  "status": "done | need_confirm",
  "running_command_label": "Кратко: что делается прямо сейчас (с эмодзи)",
  "actions": [...]
}
"""


CHAT_ONLY_PROMPT = """
Direct-answer mode is active for this message.
- Do not call tools.
- Do not return JSON actions.
- Do not inspect files or invent tool results.
- Follow the user's requested output shape exactly.
"""
