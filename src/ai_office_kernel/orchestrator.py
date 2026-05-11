from __future__ import annotations

from dataclasses import dataclass

from ai_office_kernel.cli.base import BaseCLIAdapter, CLIResult
from ai_office_kernel.config import Settings
from ai_office_kernel.fs import WorkspaceTools
from ai_office_kernel.local import OllamaClient, OllamaError
from ai_office_kernel.memory import ChatMessage, SharedMemory
from ai_office_kernel.roles import DEFAULT_ROLES, RoleId, role_prompt
from ai_office_kernel.router import Router


@dataclass(frozen=True)
class KernelResponse:
    role_id: RoleId
    prefix: str
    text: str
    reason: str


class AgentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        *,
        router: Router | None = None,
        memory: SharedMemory | None = None,
        ollama: OllamaClient | None = None,
        developer_adapter: BaseCLIAdapter | None = None,
        qa_adapter: BaseCLIAdapter | None = None,
        workspace: WorkspaceTools | None = None,
    ):
        self.settings = settings
        self.router = router or Router()
        self.memory = memory or SharedMemory(settings.memory_messages)
        self.ollama = ollama or OllamaClient(
            base_url=settings.ollama_base_url,
            default_model=settings.router_model,
        )
        self.developer_adapter = developer_adapter
        self.qa_adapter = qa_adapter
        self.workspace = workspace or WorkspaceTools(settings.workspace_root)

    def handle_text(self, chat_id: int, user_id: int | None, text: str) -> KernelResponse:
        self.memory.add(chat_id, ChatMessage(role="user", content=text, user_id=user_id))
        decision = self.router.route(text)
        response_text = self._dispatch(chat_id, decision.role_id, decision.task_text, decision.run_qa)
        role = DEFAULT_ROLES[decision.role_id]
        self.memory.add(chat_id, ChatMessage(role=role.label, content=response_text))
        return KernelResponse(
            role_id=decision.role_id,
            prefix=role.prefix,
            text=response_text,
            reason=decision.reason,
        )

    def _dispatch(
        self,
        chat_id: int,
        role_id: RoleId,
        task_text: str,
        run_qa: bool,
    ) -> str:
        if role_id == "secretary":
            return self._ask_secretary(chat_id, task_text)
        if role_id == "developer":
            return self._ask_developer(chat_id, task_text, run_qa=run_qa)
        return self._ask_qa(chat_id, task_text)

    def _ask_secretary(self, chat_id: int, task_text: str) -> str:
        context = self.memory.context_text(chat_id, limit=8)
        prompt = f"Recent chat context:\n{context}\n\nUser task:\n{task_text}"
        try:
            text = self.ollama.generate(
                prompt,
                system=role_prompt("secretary", self.settings.prompt_dir),
            )
            if text:
                return text
            return "Local Ollama returned an empty response."
        except OllamaError as exc:
            return f"Local Ollama is unavailable: {exc}"

    def _ask_developer(self, chat_id: int, task_text: str, *, run_qa: bool) -> str:
        context = self.memory.context_text(chat_id, limit=10)
        if self.settings.developer_backend == "local":
            dev_text = self._ask_local_role(
                chat_id,
                "developer",
                task_text,
                model=self.settings.local_coder_model,
            )
        else:
            if self.developer_adapter is None:
                raise RuntimeError("developer_adapter is not configured")
            self._free_local_model()
            prompt = f"Shared context:\n{context}\n\nDeveloper task:\n{task_text}"
            dev_result = self.developer_adapter.ask(prompt)
            dev_text = self._result_text(dev_result)
        if not run_qa or not self.settings.qa_enabled or self.qa_adapter is None:
            if not run_qa or not self.settings.qa_enabled:
                return dev_text
            if self.settings.qa_backend != "local":
                return dev_text
        if run_qa and self.settings.qa_enabled:
            qa_text = self._review_developer_answer(chat_id, task_text, dev_text)
            return f"{dev_text}\n\nQA review:\n{qa_text}"
        return dev_text

    def _review_developer_answer(
        self,
        chat_id: int,
        task_text: str,
        dev_text: str,
    ) -> str:
        qa_prompt = (
            "Review the developer answer before it is sent to the user. "
            "Focus on correctness, security, and missing tests.\n\n"
            f"User task:\n{task_text}\n\nDeveloper answer:\n{dev_text}"
        )
        if self.settings.qa_backend == "local":
            return self._ask_local_role(
                chat_id,
                "qa",
                qa_prompt,
                model=self.settings.local_qa_model,
            )
        if self.qa_adapter is None:
            return dev_text
        qa_result = self.qa_adapter.ask(qa_prompt)
        return self._result_text(qa_result)

    def _ask_qa(self, chat_id: int, task_text: str) -> str:
        if self.settings.qa_backend == "local":
            return self._ask_local_role(
                chat_id,
                "qa",
                task_text,
                model=self.settings.local_qa_model,
            )
        if self.qa_adapter is None:
            raise RuntimeError("qa_adapter is not configured")
        self._free_local_model()
        context = self.memory.context_text(chat_id, limit=10)
        prompt = f"Shared context:\n{context}\n\nQA task:\n{task_text}"
        return self._result_text(self.qa_adapter.ask(prompt))

    def _ask_local_role(
        self,
        chat_id: int,
        role_id: RoleId,
        task_text: str,
        *,
        model: str,
    ) -> str:
        context = self.memory.context_text(chat_id, limit=10)
        prompt = f"Shared context:\n{context}\n\nTask:\n{task_text}"
        try:
            text = self.ollama.generate(
                prompt,
                model=model,
                system=role_prompt(role_id, self.settings.prompt_dir),
            )
            if text:
                return text
            return f"Local Ollama model returned an empty response for role {role_id}: {model}"
        except OllamaError as exc:
            return f"Local Ollama model is unavailable for role {role_id}: {model}. Error: {exc}"

    def _free_local_model(self) -> None:
        try:
            self.ollama.unload_model(self.settings.router_model)
        except OllamaError:
            pass

    def _result_text(self, result: CLIResult) -> str:
        text: str
        if result.timed_out:
            text = result.output or "CLI adapter timed out before producing output."
        elif result.exit_code not in (0, None) and result.output:
            text = f"{result.output}\n\nCLI exit code: {result.exit_code}"
        else:
            text = result.output or "CLI adapter returned no output."

        usage = result.usage_summary() if self.settings.show_usage else ""
        if usage:
            suffix = f"Usage: {usage}"
            if result.session_id:
                suffix += f" session={result.session_id}"
            text = f"{text}\n\n{suffix}"
        return text
