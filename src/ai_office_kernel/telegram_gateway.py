from __future__ import annotations

import asyncio
import html
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic
from typing import Callable, TypeVar

from telegram.constants import ChatAction

from ai_office_kernel.agent_loop import (
    AgentPendingConfirmation,
    AgentRunResult,
    SecretaryAgentLoop,
)
from ai_office_kernel.factory import build_orchestrator
from ai_office_kernel.orchestrator import AgentOrchestrator, KernelResponse
from ai_office_kernel.task_flow import TaskBackend, TaskSession
from ai_office_kernel.tools import (
    BackgroundProcess,
    CommandResult,
    PendingCommand,
    ToolError,
    ToolRuntime,
    WorkspaceToolRunner,
)
from ai_office_kernel.voice import VoiceProcessor

TELEGRAM_LIMIT = 3900
ROLE_COMMANDS = ("dev", "qa", "sec", "manager")
TASK_COMMANDS = (
    "start",
    "help",
    "task",
    "status",
    "local",
    "gemini",
    "model",
    "confirm",
    "confirm_model",
    "run",
    "cancel",
    "tools",
    "workspace",
    "pwd",
    "ls",
    "read",
    "scan",
    "export",
    "cmd",
    "bg",
    "confirm_tool",
    "cancel_tool",
    "confirm_agent",
    "cancel_agent",
    "procs",
    "stop",
    "voice",
)
RUNNING_ALLOWED_COMMANDS = {"status", "procs", "stop", "cancel_tool", "confirm_agent", "cancel_agent"}

HELP_TEXT = """Команды:
Обычный текст - главный режим: общение с локальным секретарем, авто-tools и маршрутизация
/task <текст> - fallback: создать ручную активную задачу
/local - выполнять активную задачу локальной Ollama-моделью
/gemini - выполнять активную задачу через Gemini CLI
/model <model> - запросить смену модели Gemini CLI
/run - подготовить запуск активной задачи
/confirm - подтвердить запуск или ожидающую смену модели
/confirm_agent - подтвердить опасное действие агентного секретаря
/status - показать активную задачу или долгий запуск
/cancel - отменить активную задачу
/voice - переключить озвучку ответов
/tools - локальные tools: файлы и команды
/workspace - показать workspace root
/ls [path], /read <file>, /scan [path], /export <file>, /cmd <command>, /bg <command>, /procs, /stop <id>

Прямые роли для debug тоже работают: /sec, /dev, /qa."""

TOOL_HELP_TEXT = """Локальные tools:
/pwd - показать workspace
/workspace - показать workspace root и алиас /workspace
/ls [path] - список файлов внутри workspace
/read <file> - прочитать файл внутри workspace
/scan [path] - проверить проект на вероятные токены/секреты перед публикацией
/export <file> - выгрузить файл из workspace в чат (скачать)
/cmd [--cwd path] [--timeout sec] <command> - подготовить foreground-команду
/bg [--cwd path] <command> - подготовить фоновую команду, например dev server
/confirm_tool - реально выполнить подготовленную команду
/confirm_agent - подтвердить опасное действие агентного секретаря
/cancel_tool - отменить подготовленную команду
/procs - показать фоновые процессы
/stop <id> - остановить фоновый процесс

Ручные команды требуют /confirm_tool. Агентный секретарь может сам запускать safe/medium tools внутри workspace.
Опасные действия и Gemini CLI escalation требуют /confirm_agent или обычного ответа "да"."""


T = TypeVar("T")


@dataclass
class RunningJob:
    title: str
    started_at: float
    backend: str | None = None
    model: str | None = None
    current_status: str | None = None

    def status_text(self) -> str:
        elapsed = _format_elapsed(monotonic() - self.started_at)
        status = self.current_status or "анализ задачи..."
        
        lines = [
            f"<b>⚙️ {self.title.upper()}</b>",
            f"⏳ В процессе: <code>{elapsed}</code>",
            f"🎯 Статус: <b>{status}</b>"
        ]
        
        if self.backend or self.model:
            details = []
            if self.backend: details.append(f"📦 {self.backend}")
            if self.model: details.append(f"🤖 {self.model}")
            lines.append(" | ".join(details))
            
        return "\n".join(lines)


class TelegramGateway:
    def __init__(self, orchestrator: AgentOrchestrator, token: str, allowed_chat_ids: Iterable[int] = ()):
        self.orchestrator = orchestrator
        self.token = token
        self.allowed_chat_ids = set(allowed_chat_ids)
        self.tasks: dict[int, TaskSession] = {}
        self.chat_gemini_models: dict[int, str] = {}
        self.pending_chat_gemini_models: dict[int, str] = {}
        self.running_jobs: dict[int, RunningJob] = {}
        self.pending_tools: dict[int, PendingCommand] = {}
        self.pending_agent_confirmations: dict[int, AgentPendingConfirmation] = {}
        self.tool_runner = WorkspaceToolRunner(orchestrator.settings.workspace_root)
        self.voice_processor = VoiceProcessor(orchestrator.settings.whisper_model)
        self.chat_voice_mode: dict[int, bool] = {}
        self.agent_loop = SecretaryAgentLoop(
            orchestrator.settings,
            ollama=orchestrator.ollama,
            memory=orchestrator.memory,
            tool_runtime_factory=self._build_tool_runtime,
        )

    def run(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
        except ImportError as exc:
            raise RuntimeError(
                "python-telegram-bot is not installed. Run: pip install -e ."
            ) from exc

        async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat is None or update.effective_message is None:
                return
            if not self._chat_allowed(update.effective_chat.id):
                return
            text = update.effective_message.text or ""
            if not text.strip():
                return
            self.chat_voice_mode[update.effective_chat.id] = False
            await self._reply_agent(update, text)

        async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat is None or update.effective_message is None or update.effective_message.voice is None:
                return
            if not self._chat_allowed(update.effective_chat.id):
                return
            
            chat_id = update.effective_chat.id
            self.chat_voice_mode[chat_id] = True
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            
            voice = update.effective_message.voice
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                voice_path = tmp.name
            
            try:
                file = await context.bot.get_file(voice.file_id)
                await file.download_to_drive(voice_path)
                
                text = await asyncio.to_thread(self.voice_processor.transcribe, voice_path)
                if not text:
                    await update.effective_message.reply_text("Не удалось распознать голос.")
                    return
                
                await update.effective_message.reply_text(f"🎤 {text}")
                await self._reply_agent(update, text)
            except Exception as e:
                await update.effective_message.reply_text(f"Ошибка обработки голоса: {e}")
            finally:
                if os.path.exists(voice_path):
                    os.remove(voice_path)

        async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat is None or update.effective_message is None or update.effective_message.document is None:
                return
            if not self._chat_allowed(update.effective_chat.id):
                return
            
            chat_id = update.effective_chat.id
            doc = update.effective_message.document
            file_name = doc.file_name or f"file_{doc.file_id}"
            file_name = os.path.basename(file_name)
            
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            target_path = self.tool_runner.workspace_root / file_name
            
            try:
                file = await context.bot.get_file(doc.file_id)
                await file.download_to_drive(str(target_path))
                await update.effective_message.reply_text(
                    f"📥 Файл <code>{file_name}</code> сохранен в workspace.", 
                    parse_mode="HTML"
                )
                await self._reply_agent(update, f"Я получил файл: {file_name}")
            except Exception as e:
                await update.effective_message.reply_text(f"Ошибка загрузки: {e}")

        async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_chat is None or update.effective_message is None:
                return
            if not self._chat_allowed(update.effective_chat.id):
                return
            command = update.effective_message.text or ""
            command_name, rest = _parse_command(command)
            if command_name in TASK_COMMANDS:
                await self._handle_task_command(update, command_name, rest)
                return
            text = "@" + command_name + (" " + rest if rest else "")
            await self._reply_kernel(update, text)

        app = Application.builder().token(self.token).concurrent_updates(True).build()
        app.add_handler(CommandHandler([*ROLE_COMMANDS, *TASK_COMMANDS], handle_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        app.add_handler(MessageHandler(filters.VOICE, handle_voice))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.run_polling(allowed_updates=Update.ALL_TYPES)

    def _chat_allowed(self, chat_id: int) -> bool:
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids

    async def _reply_kernel(self, update, text: str) -> None:
        chat_id = update.effective_chat.id
        if await self._reply_running_job(update):
            return
        user_id = update.effective_user.id if update.effective_user else None
        response = await self._run_with_progress(
            update,
            RunningJob("обработка сообщения", monotonic()),
            self._handle_kernel_text,
            chat_id,
            user_id,
            text,
        )
        for chunk in _format_response(response):
            await update.effective_message.reply_text(chunk)

    async def _reply_agent(self, update, text: str) -> None:
        chat_id = update.effective_chat.id
        if await self._maybe_handle_model_confirmation(update, text):
            return
        if await self._maybe_handle_agent_confirmation(update, text):
            return
        if await self._reply_running_job(update):
            return
        user_id = update.effective_user.id if update.effective_user else None
        agent_text = self._agent_input(chat_id, text)
        
        job = RunningJob(
            "агентный секретарь", 
            monotonic(), 
            backend="local", 
            model=self.orchestrator.settings.router_model
        )
        
        result = await self._run_with_progress(
            update,
            job,
            self.agent_loop.run,
            chat_id,
            user_id,
            agent_text,
            on_status=lambda s: setattr(job, 'current_status', s)
        )
        await self._reply_agent_result(update, result)

    def _agent_input(self, chat_id: int, text: str) -> str:
        session = self.tasks.get(chat_id)
        if session is None:
            return text
        session.add_note(text)
        return f"Active task context:\n{session.full_prompt()}\n\nNew user message:\n{text}"

    def _handle_kernel_text(
        self,
        chat_id: int,
        user_id: int | None,
        text: str,
    ) -> KernelResponse:
        model = self.chat_gemini_models.get(chat_id)
        runner = self._runner_for(gemini_model=model)
        return runner.handle_text(chat_id, user_id, text)

    async def _reply_task_discussion(self, update, text: str) -> None:
        chat_id = update.effective_chat.id
        if await self._reply_running_job(update):
            return
        user_id = update.effective_user.id if update.effective_user else None
        session = self.tasks[chat_id]
        session.add_note(text)
        if _looks_like_tool_request(text):
            await self._reply_text(
                update,
                "[Secretary]: Я не запускаю команды сам и не буду притворяться, что localhost уже работает.\n\n"
                "Для реального запуска используй tools:\n"
                "/ls - найти папку проекта\n"
                "/cmd --cwd <project-dir> npm install - подготовить установку зависимостей\n"
                "/bg --cwd <project-dir> npm run dev -- --host 0.0.0.0 - подготовить dev-server\n"
                "/confirm_tool - подтвердить подготовленную команду\n"
                "/procs - проверить фоновые процессы",
            )
            return
        prompt = (
            "@sec Мы обсуждаем активную задачу. Твоя цель - из неформальных сообщений "
            "собрать четкое ТЗ для разработчика. Кратко ответь в формате:\n"
            "1. Что уже понятно\n"
            "2. Что надо уточнить одним следующим вопросом\n"
            "3. Когда можно запускать: /local или /gemini, затем /run и /confirm\n\n"
            "Не утверждай, что ты запускал команды, создавал файлы или проверял localhost.\n\n"
            f"{session.full_prompt()}"
        )
        response = await self._run_with_progress(
            update,
            RunningJob("обсуждение активной задачи с секретарем", monotonic(), backend="local"),
            self._handle_kernel_text,
            chat_id,
            user_id,
            prompt,
        )
        for chunk in _format_response(response):
            await update.effective_message.reply_text(chunk)

    async def _handle_task_command(self, update, command: str, rest: str) -> None:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id if update.effective_user else None

        if command in {"start", "help"}:
            await self._reply_text(update, HELP_TEXT)
            return
        if command == "task":
            await self._start_task(update, rest)
            return
        if command == "status":
            await self._reply_text(update, self._status_text(chat_id))
            return
        if command in {
            "tools",
            "workspace",
            "pwd",
            "ls",
            "read",
            "scan",
            "cmd",
            "bg",
            "confirm_tool",
            "cancel_tool",
            "confirm_agent",
            "cancel_agent",
            "procs",
            "stop",
        }:
            await self._handle_tool_command(update, command, rest)
            return
        if command not in RUNNING_ALLOWED_COMMANDS and chat_id in self.running_jobs:
            await self._reply_text(
                update,
                self.running_jobs[chat_id].status_text()
                + "\n\nДождись завершения или смотри статус через /status.",
            )
            return
        if command == "cancel":
            await self._cancel(update)
            return
        if command in {"local", "gemini"}:
            await self._select_backend(update, command)  # type: ignore[arg-type]
            return
        if command == "model":
            await self._request_model_change(update, rest)
            return
        if command == "confirm_model":
            await self._confirm_model(update)
            return
        if command == "run":
            await self._request_run(update)
            return
        if command == "voice":
            current = self.chat_voice_mode.get(chat_id, False)
            new_mode = not current
            self.chat_voice_mode[chat_id] = new_mode
            status = "включена" if new_mode else "выключена"
            await self._reply_text(update, f"Озвучка ответов {status}.")
            return
        if command == "confirm":
            if self._has_pending_model(chat_id):
                await self._confirm_model(update)
                return
            await self._confirm_run(update, user_id)

    async def _start_task(self, update, description: str) -> None:
        chat_id = update.effective_chat.id
        description = description.strip()
        if not description:
            await self._reply_text(update, "Напиши так: /task <что нужно сделать>")
            return
        model = self.chat_gemini_models.get(chat_id, self.orchestrator.settings.gemini_model)
        self.tasks[chat_id] = TaskSession(description=description, gemini_model=model)
        await self._reply_text(
            update,
            "Задачу записал. Обсуждай детали обычными сообщениями.\n\n"
            "Перед запуском выбери backend: /local или /gemini.\n"
            f"Текущая модель Gemini CLI: {model}. Сменить: /model <model>.",
        )

    async def _select_backend(self, update, backend: TaskBackend) -> None:
        session = await self._require_task(update)
        if session is None:
            return
        session.set_backend(backend)
        if backend == "local":
            await self._reply_text(
                update,
                "Выбран локальный запуск через Ollama. Для выполнения: /run.",
            )
            return
        await self._reply_text(
            update,
            "Выбран запуск через Gemini CLI.\n"
            f"Модель: {session.gemini_model}\n"
            "Перед запуском можно сменить модель: /model <model>.\n"
            "Для выполнения: /run.",
        )

    async def _request_model_change(self, update, model: str) -> None:
        chat_id = update.effective_chat.id
        model = model.strip()
        if not model:
            current = self._current_gemini_model(chat_id)
            await self._reply_text(
                update,
                f"Текущая модель Gemini CLI: {current}\n"
                "Смена модели: /model auto или /model <gemini-model>\n"
                "После этого подтвердить: /confirm_model",
            )
            return

        session = self.tasks.get(chat_id)
        if session is not None:
            session.request_model_change(model)
            await self._reply_text(
                update,
                f"Подтверди смену модели для активной задачи на {model}: /confirm_model",
            )
            return

        self.pending_chat_gemini_models[chat_id] = model
        await self._reply_text(
            update,
            f"Подтверди смену модели Gemini CLI для этого чата на {model}: /confirm_model",
        )

    async def _confirm_model(self, update, *, quiet: bool = False) -> bool:
        chat_id = update.effective_chat.id
        session = self.tasks.get(chat_id)
        if session is not None and session.pending_model:
            model = session.confirm_model_change()
            if not quiet:
                await self._reply_text(update, f"Модель активной задачи изменена: {model}")
            return True

        pending = self.pending_chat_gemini_models.pop(chat_id, None)
        if pending:
            self.chat_gemini_models[chat_id] = pending
            if session is not None:
                session.gemini_model = pending
            if not quiet:
                await self._reply_text(update, f"Модель Gemini CLI для этого чата изменена: {pending}")
            return True

        if not quiet:
            await self._reply_text(update, "Нет ожидающей смены модели.")
        return False

    async def _request_run(self, update) -> None:
        session = await self._require_task(update)
        if session is None:
            return
        if session.backend is None:
            await self._reply_text(update, "Сначала выбери backend: /local или /gemini.")
            return
        session.request_run()
        if session.backend == "gemini":
            await self._reply_text(
                update,
                "Подтверди запуск задачи через Gemini CLI.\n"
                f"Модель: {session.gemini_model}\n"
                "Если модель не та, сначала используй /model <model>.\n"
                "Запустить: /confirm",
            )
            return
        await self._reply_text(
            update,
            "Подтверди запуск задачи через локальную Ollama-модель: /confirm",
        )

    async def _confirm_run(self, update, user_id: int | None) -> None:
        session = await self._require_task(update)
        if session is None:
            return
        if not session.consume_run_confirmation():
            await self._reply_text(update, "Нет ожидающего запуска. Сначала: /run.")
            return

        await self._reply_text(update, "Запускаю задачу. Ответ придет следующим сообщением.")
        chat_id = update.effective_chat.id
        response = await self._run_with_progress(
            update,
            RunningJob(
                "выполнение активной задачи",
                monotonic(),
                backend=session.backend,
                model=session.gemini_model if session.backend == "gemini" else None,
            ),
            self._run_task,
            chat_id,
            user_id,
            session,
        )
        for chunk in _format_response(response):
            await update.effective_message.reply_text(chunk)

    def _run_task(
        self,
        chat_id: int,
        user_id: int | None,
        session: TaskSession,
    ) -> KernelResponse:
        runner = self._runner_for(
            developer_backend=session.backend or self.orchestrator.settings.developer_backend,
            gemini_model=session.gemini_model,
            qa_enabled=False,
        )
        return runner.handle_text(chat_id, user_id, "@dev " + session.execution_prompt())

    async def _cancel(self, update) -> None:
        chat_id = update.effective_chat.id
        if self.tasks.pop(chat_id, None) is not None:
            await self._reply_text(update, "Активная задача отменена.")
            return
        if self.pending_chat_gemini_models.pop(chat_id, None) is not None:
            await self._reply_text(update, "Ожидающая смена модели отменена.")
            return
        if self.pending_tools.pop(chat_id, None) is not None:
            await self._reply_text(update, "Ожидающая tool-команда отменена.")
            return
        if self.pending_agent_confirmations.pop(chat_id, None) is not None:
            await self._reply_text(update, "Ожидающее действие агентного секретаря отменено.")
            return
        await self._reply_text(update, "Нет активной задачи.")

    async def _handle_tool_command(self, update, command: str, rest: str) -> None:
        chat_id = update.effective_chat.id
        try:
            if command == "tools":
                await self._reply_text(update, TOOL_HELP_TEXT)
                return
            if command in {"pwd", "workspace"}:
                await self._reply_text(
                    update,
                    f"[WORKSPACE]\nRoot: {self.tool_runner.workspace_root}\nAlias: /workspace",
                )
                return
            if command == "ls":
                await self._reply_text(update, self.tool_runner.list_dir(rest or "."))
                return
            if command == "read":
                if not rest.strip():
                    await self._reply_text(update, "Формат: /read <file>")
                    return
                await self._reply_text(update, self.tool_runner.read_file(rest))
                return
            if command == "export":
                if not rest.strip():
                    await self._reply_text(update, "Формат: /export <file>")
                    return
                runtime = self._build_tool_runtime(chat_id)
                request = runtime.request_from_action(
                    {"tool": "export_file", "args": {"path": rest.strip()}}
                )
                result = runtime.execute(request)
                if result.content.startswith("[EXPORT] "):
                    path_str = result.content.removeprefix("[EXPORT] ").strip()
                    path = Path(path_str)
                    if path.exists() and path.is_file():
                        with open(path, "rb") as f:
                            await update.effective_message.reply_document(
                                document=f,
                                caption=f"📦 Выгрузка: <code>{path.name}</code>",
                                parse_mode="HTML"
                            )
                        return
                await self._reply_text(update, result.content)
                return
            if command == "scan":
                runtime = self._build_tool_runtime(chat_id)
                request = runtime.request_from_action(
                    {"tool": "scan_secrets", "args": {"path": rest.strip() or "."}}
                )
                result = runtime.execute(request)
                await self._reply_text(update, result.content)
                return
            if command in {"cmd", "bg"}:
                if not rest.strip():
                    await self._reply_text(update, f"Формат: /{command} [--cwd path] <command>")
                    return
                pending = self.tool_runner.prepare_command(
                    rest,
                    background=command == "bg",
                    default_timeout_seconds=120,
                )
                self.pending_tools[chat_id] = pending
                await self._reply_text(update, pending.confirm_text())
                return
            if command == "confirm_tool":
                pending = self.pending_tools.pop(chat_id, None)
                if pending is None:
                    await self._reply_text(update, "Нет ожидающей tool-команды.")
                    return
                result = await asyncio.to_thread(self.tool_runner.run_command, pending)
                await self._reply_text(update, _tool_result_text(result))
                return
            if command == "cancel_tool":
                if self.pending_tools.pop(chat_id, None) is None:
                    await self._reply_text(update, "Нет ожидающей tool-команды.")
                else:
                    await self._reply_text(update, "Tool-команда отменена.")
                return
            if command == "confirm_agent":
                pending = self.pending_agent_confirmations.pop(chat_id, None)
                if pending is None:
                    await self._reply_text(update, "Нет ожидающего действия агентного секретаря.")
                    return
                
                job = RunningJob("подтвержденное действие секретаря", monotonic(), backend="local")
                result = await self._run_with_progress(
                    update,
                    job,
                    self.agent_loop.resume_confirmed,
                    pending,
                    on_status=lambda s: setattr(job, 'current_status', s)
                )
                await self._reply_agent_result(update, result)
                return
            if command == "cancel_agent":
                if self.pending_agent_confirmations.pop(chat_id, None) is None:
                    await self._reply_text(update, "Нет ожидающего действия агентного секретаря.")
                else:
                    await self._reply_text(update, "Действие агентного секретаря отменено.")
                return
            if command == "procs":
                await self._reply_text(update, self.tool_runner.process_status())
                return
            if command == "stop":
                if not rest.strip():
                    await self._reply_text(update, "Формат: /stop <process_id>")
                    return
                await self._reply_text(update, self.tool_runner.stop_process(int(rest.strip())))
                return
        except (ToolError, ValueError) as exc:
            await self._reply_text(update, f"[TOOL ERROR]\n{exc}")

    async def _require_task(self, update) -> TaskSession | None:
        session = self.tasks.get(update.effective_chat.id)
        if session is None:
            await self._reply_text(update, "Сначала создай задачу: /task <описание>")
        return session

    def _status_text(self, chat_id: int) -> str:
        running = self.running_jobs.get(chat_id)
        session = self.tasks.get(chat_id)
        model = self._current_gemini_model(chat_id)
        if running is not None and session is not None:
            return running.status_text() + "\n\n" + session.status_text()
        if running is not None:
            return running.status_text()
        if session is None:
            return (
                "Активной задачи нет.\n"
                f"Текущая модель Gemini CLI для чата: {model}\n"
                "Создать задачу: /task <описание>"
            )
        return session.status_text()

    def _current_gemini_model(self, chat_id: int) -> str:
        return self.chat_gemini_models.get(chat_id, self.orchestrator.settings.gemini_model)

    def _has_pending_model(self, chat_id: int) -> bool:
        session = self.tasks.get(chat_id)
        return bool(session and session.pending_model) or chat_id in self.pending_chat_gemini_models

    def _runner_for(
        self,
        *,
        developer_backend: str | None = None,
        gemini_model: str | None = None,
        qa_enabled: bool | None = None,
    ) -> AgentOrchestrator:
        overrides = {}
        if developer_backend is not None:
            overrides["developer_backend"] = developer_backend
        if gemini_model is not None:
            overrides["gemini_model"] = gemini_model
        if qa_enabled is not None:
            overrides["qa_enabled"] = qa_enabled
        if not overrides:
            return self.orchestrator

        settings = replace(self.orchestrator.settings, **overrides)
        runner = build_orchestrator(settings)
        runner.memory = self.orchestrator.memory
        runner.router = self.orchestrator.router
        runner.ollama = self.orchestrator.ollama
        return runner

    def _build_tool_runtime(self, chat_id: int) -> ToolRuntime:
        return ToolRuntime(
            self.orchestrator.settings.workspace_root,
            workspace=self.tool_runner,
            ask_local_coder=lambda prompt: self._ask_local_coder(chat_id, prompt),
            ask_gemini_cli=lambda prompt: self._ask_gemini_cli(chat_id, prompt),
            request_gemini_model_change=lambda model: self._request_gemini_model_change(chat_id, model),
        )

    def _ask_local_coder(self, chat_id: int, prompt: str) -> str:
        runner = self._runner_for(developer_backend="local", qa_enabled=False)
        return runner.handle_text(chat_id, None, "@dev " + prompt).text

    def _ask_gemini_cli(self, chat_id: int, prompt: str) -> str:
        runner = self._runner_for(developer_backend="gemini", qa_enabled=False)
        return runner.handle_text(chat_id, None, "@dev " + prompt).text

    def _request_gemini_model_change(self, chat_id: int, model: str) -> str:
        model = model.strip()
        if not model:
            return f"Текущая модель Gemini CLI: {self._current_gemini_model(chat_id)}"
        session = self.tasks.get(chat_id)
        if session is not None:
            session.request_model_change(model)
        else:
            self.pending_chat_gemini_models[chat_id] = model
        return (
            f"Запрошена смена Gemini CLI модели на {model}. "
            "Модель не изменена до подтверждения. Подтвердить можно ответом 'да' или командой /confirm_model."
        )

    async def _reply_agent_result(self, update, result: AgentRunResult) -> None:
        chat_id = update.effective_chat.id
        status_lines = [
            _agent_event_text(event)
            for event in result.events
            if event.kind in {"tool_error", "need_confirm"}
        ]
        if status_lines:
            await self._reply_text(update, "\n\n".join(status_lines), parse_mode="HTML")
        
        # Обработка выгрузки файлов (export_file tool)
        for event in result.events:
            if event.kind == "tool_result" and event.content.startswith("[EXPORT] "):
                path_str = event.content.removeprefix("[EXPORT] ").strip()
                path = Path(path_str)
                if path.exists() and path.is_file():
                    try:
                        with open(path, "rb") as f:
                            await update.effective_message.reply_document(
                                document=f, 
                                caption=f"📦 Выгрузка: <code>{path.name}</code>",
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        await update.effective_message.reply_text(f"Ошибка при отправке файла: {e}")

        if result.pending is not None:
            self.pending_agent_confirmations[chat_id] = result.pending
            
            # Собираем детали аргументов для информативности
            args_details = ""
            if result.pending.request.args:
                for key, val in result.pending.request.args.items():
                    if key == "prompt":
                        args_details += f"<b>📝 Техническое задание (Senior):</b>\n<pre>{html.escape(str(val))}</pre>\n"
                    else:
                        args_details += f"🔹 {key}: <code>{html.escape(str(val))}</code>\n"

            confirm_msg = (
                f"<b>⚠️ ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ БОССА</b>\n\n"
                f"<b>Действие:</b> 🛠️ <code>{result.pending.request.name}</code>\n"
                f"{args_details}\n"
                f"<b>Обоснование:</b> 💡 {html.escape(result.pending.request.reason or 'не указано')}\n\n"
                f"✅ Подтвердить: /confirm_agent\n"
                f"❌ Отмена: /cancel_agent"
            )
            await self._reply_text(update, confirm_msg, parse_mode="HTML")
            return

        if self._has_pending_model(chat_id):
            session = self.tasks.get(chat_id)
            pending_model = session.pending_model if session is not None else self.pending_chat_gemini_models.get(chat_id)
            model_msg = (
                f"<b>⚙️ Смена модели</b>\n"
                f"Новая модель: <code>{pending_model}</code>\n\n"
                "Подтвердить: <code>да</code> или /confirm_model"
            )
            await self._reply_text(update, model_msg, parse_mode="HTML")

        # Основной ответ без лишних префиксов
        await self._reply_text(update, result.text, parse_mode="HTML")
        
        if self.chat_voice_mode.get(chat_id) and self.orchestrator.settings.voice_enabled:
            await self._reply_voice(update, result.text)

    async def _reply_voice(self, update, text: str) -> None:
        chat_id = update.effective_chat.id
        from telegram.constants import ChatAction
        await update.get_bot().send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)
        
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            voice_path = tmp.name
        
        try:
            # Очищаем текст от markdown-разметки для лучшего звучания
            clean_text = text.replace("*", "").replace("_", "").replace("`", "")
            await self.voice_processor.synthesize(
                clean_text, 
                voice_path, 
                voice=self.orchestrator.settings.tts_voice
            )
            with open(voice_path, "rb") as voice_file:
                await update.effective_message.reply_voice(voice=voice_file)
        except Exception as e:
            await update.effective_message.reply_text(f"Ошибка синтеза речи: {e}")
        finally:
            if os.path.exists(voice_path):
                os.remove(voice_path)

    async def _maybe_handle_model_confirmation(self, update, text: str) -> bool:
        chat_id = update.effective_chat.id
        if not self._has_pending_model(chat_id):
            return False
        normalized = text.strip().lower()
        yes_words = {"да", "yes", "y", "ok", "ок", "подтверждаю", "меняй", "применяй"}
        no_words = {"нет", "no", "n", "отмена", "отмени", "cancel", "стоп"}
        if normalized in no_words:
            session = self.tasks.get(chat_id)
            if session is not None and session.pending_model:
                session.pending_model = None
            self.pending_chat_gemini_models.pop(chat_id, None)
            await self._reply_text(update, "Смена модели Gemini CLI отменена.")
            return True
        if normalized not in yes_words:
            await self._reply_text(
                update,
                "Есть ожидающая смена модели Gemini CLI.\n"
                "Ответь `да` для применения или `нет` для отмены.",
            )
            return True
        await self._confirm_model(update, quiet=True)
        session = self.tasks.get(chat_id)
        current_model = session.gemini_model if session is not None else self._current_gemini_model(chat_id)
        await self._reply_text(update, f"Модель Gemini CLI изменена: {current_model}")
        return True

    async def _maybe_handle_agent_confirmation(self, update, text: str) -> bool:
        chat_id = update.effective_chat.id
        pending = self.pending_agent_confirmations.get(chat_id)
        if pending is None:
            return False
        normalized = text.strip().lower()
        yes_words = {"да", "yes", "y", "ok", "ок", "подтверждаю", "запускай", "выполняй"}
        no_words = {"нет", "no", "n", "отмена", "отмени", "cancel", "стоп"}
        if normalized in no_words:
            self.pending_agent_confirmations.pop(chat_id, None)
            await self._reply_text(update, "Действие агентного секретаря отменено.")
            return True
        if normalized not in yes_words:
            self.pending_agent_confirmations.pop(chat_id, None)
            await self._reply_text(
                update,
                "Ожидающее действие не запускаю. Разбираю новое сообщение.",
            )
            return False
        self.pending_agent_confirmations.pop(chat_id, None)
        job = RunningJob("подтвержденное действие секретаря", monotonic(), backend="local")
        result = await self._run_with_progress(
            update,
            job,
            self.agent_loop.resume_confirmed,
            pending,
            on_status=lambda s: setattr(job, 'current_status', s)
        )
        await self._reply_agent_result(update, result)
        return True

    async def _run_with_progress(
        self,
        update,
        job: RunningJob,
        func: Callable[..., T],
        *args,
        on_status: Callable[[str], None] | None = None,
    ) -> T:
        chat_id = update.effective_chat.id
        if chat_id in self.running_jobs:
            await self._reply_text(update, job.status_text(), parse_mode="HTML")
            raise RuntimeError("A job is already running in this chat.")

        self.running_jobs[chat_id] = job
        status_message = None
        
        # Обертка для обновления статуса
        def update_job_status(new_status: str):
            job.current_status = new_status
            if on_status:
                on_status(new_status)

        actual_args = list(args)
        import inspect
        sig = inspect.signature(func)
        if "on_status" in sig.parameters:
            kwargs = {"on_status": update_job_status}
        else:
            kwargs = {}

        task = asyncio.create_task(asyncio.to_thread(func, *actual_args, **kwargs))
        wait_seconds = self.orchestrator.settings.progress_first_seconds
        
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=wait_seconds)
                if done:
                    # Если задача завершена, удаляем статусное сообщение (по желанию) или просто выходим
                    if status_message:
                        try:
                            await status_message.delete()
                        except:
                            pass
                    return task.result()
                
                status_text = job.status_text()
                if not status_message:
                    status_message = await update.effective_message.reply_text(status_text, parse_mode="HTML")
                else:
                    try:
                        # Редактируем только если текст изменился (минимум изменений)
                        if status_message.text_html != status_text:
                            await status_message.edit_text(status_text, parse_mode="HTML")
                    except Exception:
                        # Если не удалось отредактировать (например, сообщение удалено), шлем новое
                        status_message = await update.effective_message.reply_text(status_text, parse_mode="HTML")
                
                wait_seconds = self.orchestrator.settings.progress_interval_seconds
        finally:
            if self.running_jobs.get(chat_id) is job:
                self.running_jobs.pop(chat_id, None)

    async def _reply_running_job(self, update) -> bool:
        running = self.running_jobs.get(update.effective_chat.id)
        if running is None:
            return False
        await self._reply_text(
            update,
            running.status_text() + "\n\nНовый запрос не запущен, потому что в этом чате уже идет задача.",
        )
        return True

    async def _reply_text(self, update, text: str, parse_mode: str | None = None) -> None:
        import html
        for chunk in _split_text(text):
            # Если это HTML режим, но текст не похож на HTML, экранируем его (защита от краша)
            if parse_mode == "HTML" and "<" not in chunk and ">" not in chunk:
                # В данном случае мы доверяем агенту, но если он шлет спецсимволы, Telegram упадет.
                # Поэтому здесь мы просто шлем как есть, но добавляем обертку для безопасности в будущем.
                pass
            await update.effective_message.reply_text(chunk, parse_mode=parse_mode)


def _format_response(response: KernelResponse) -> list[str]:
    text = f"{response.prefix}\n{response.text}".strip()
    if len(text) <= TELEGRAM_LIMIT:
        return [text]
    return [text[i : i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)]


def _split_text(text: str) -> list[str]:
    if len(text) <= TELEGRAM_LIMIT:
        return [text]
    return [text[i : i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)]


def _parse_command(text: str) -> tuple[str, str]:
    parts = text.split(maxsplit=1)
    if not parts:
        return "", ""
    command = parts[0].split("@", maxsplit=1)[0].lstrip("/").lower()
    rest = parts[1] if len(parts) > 1 else ""
    return command, rest


def _tool_result_text(result: CommandResult | BackgroundProcess) -> str:
    if isinstance(result, BackgroundProcess):
        return result.status_text() + "\n\nФоновый процесс запущен. Проверить: /procs"
    return result.text()


def _agent_event_text(event) -> str:
    if event.kind == "status":
        return f"⏳ <b>{event.label}</b>"
    if event.kind == "need_confirm":
        return (
            f"<b>🤔 Нужно подтверждение</b>\n"
            f"🎯 {event.label}\n"
            f"Инструмент: <code>{event.tool or '-'}</code>"
        )
    if event.kind == "tool_error":
        content = event.content.strip()
        if len(content) > 1200:
            content = content[:1200] + "\n... truncated ..."
        return f"❌ <b>Ошибка: {event.label}</b>\n<pre>{html.escape(content)}</pre>".strip()
    if event.tool:
        return f"🛠️ <b>{event.label}</b>"
    return f"🔹 {event.label}"


def _looks_like_tool_request(text: str) -> bool:
    lower = text.lower()
    markers = (
        "запусти",
        "запустить",
        "проверь",
        "проверить",
        "localhost",
        "локалхост",
        "команд",
        "терминал",
        "npm",
        "сервер",
        "умеешь запускать",
    )
    return any(marker in lower for marker in markers)


def _format_elapsed(seconds: float) -> str:
    seconds_int = max(0, int(seconds))
    minutes, seconds_left = divmod(seconds_int, 60)
    if minutes < 1:
        return f"{seconds_left}s"
    hours, minutes_left = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes_left}m"
    return f"{minutes}m {seconds_left}s"
