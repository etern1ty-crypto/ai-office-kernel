from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Literal, Sequence

from ai_office_kernel.cli.base import BaseCLIAdapter, CLIResult, clean_terminal_output

OutputFormat = Literal["text", "json", "stream-json"]
ApprovalMode = Literal["default", "auto_edit", "yolo", "plan"]
AUTH_REQUIRED_MESSAGE = (
    "Gemini CLI needs browser authentication in this shell. Run `NO_BROWSER=1 gemini` "
    "manually, open the printed URL, finish Google login, then restart the bot."
)
AUTH_PROMPT_MARKERS = (
    "Opening authentication page in your browser",
    "Login with Google",
    "Select Auth Method",
    "Authenticate with Google",
)


class GeminiCLIAdapter(BaseCLIAdapter):
    def __init__(
        self,
        command: str = "gemini",
        system_prompt: str = "",
        model: str = "auto",
        output_format: OutputFormat = "json",
        approval_mode: ApprovalMode = "auto_edit",
        skip_trust: bool = True,
        sandbox: bool = False,
        all_files: bool = False,
        include_directories: Sequence[str] = (),
        allowed_tools: Sequence[str] = (),
        resume: str | None = None,
        extra_args: Sequence[str] = (),
        timeout_seconds: int = 180,
        auto_confirm: bool = True,
    ):
        super().__init__(
            command=command,
            timeout_seconds=timeout_seconds,
            auto_confirm=auto_confirm,
        )
        self.system_prompt = system_prompt.strip()
        self.model = model
        self.output_format = output_format
        self.approval_mode = approval_mode
        self.skip_trust = skip_trust
        self.sandbox = sandbox
        self.all_files = all_files
        self.include_directories = tuple(include_directories)
        self.allowed_tools = tuple(allowed_tools)
        self.resume = resume
        self.extra_args = tuple(extra_args)

    def build_command(self, prompt: str) -> Sequence[str]:
        full_prompt = self._full_prompt(prompt)
        command = self._split_command()
        args = [
            *command,
            "--prompt",
            full_prompt,
            "--output-format",
            self.output_format,
        ]
        if self.model:
            args.extend(["--model", self.model])
        if self.approval_mode:
            args.extend(["--approval-mode", self.approval_mode])
        if self.skip_trust:
            args.append("--skip-trust")
        if self.sandbox:
            args.append("--sandbox")
        if self.all_files:
            args.append("--all-files")
        if self.include_directories:
            args.extend(["--include-directories", ",".join(self.include_directories)])
        for tool in self.allowed_tools:
            args.extend(["--allowed-tools", tool])
        if self.resume:
            args.extend(["--resume", self.resume])
        args.extend(self.extra_args)
        return args

    def ask(self, prompt: str) -> CLIResult:
        result = super().ask(prompt)
        if looks_like_auth_prompt(result.raw_output):
            return replace(
                result,
                output=AUTH_REQUIRED_MESSAGE,
                error={"type": "auth_required", "message": AUTH_REQUIRED_MESSAGE},
            )
        if result.timed_out:
            return result
        if self.output_format == "json":
            return parse_json_result(result)
        if self.output_format == "stream-json":
            return parse_stream_json_result(result)
        return result

    def _full_prompt(self, prompt: str) -> str:
        if not self.system_prompt:
            return prompt
        return f"{self.system_prompt}\n\nTask:\n{prompt}"


def parse_json_result(result: CLIResult) -> CLIResult:
    payload = _load_json_object(result.stdout)
    if payload is None:
        return result

    response = str(payload.get("response") or "").strip()
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    output = response
    if error and not output:
        output = str(error.get("message") or error)
    if warnings:
        output = (output + "\n\nWarnings:\n" + "\n".join(map(str, warnings))).strip()

    return replace(
        result,
        output=output or clean_terminal_output(result.stdout or result.raw_output),
        session_id=payload.get("session_id")
        if isinstance(payload.get("session_id"), str)
        else None,
        stats=payload.get("stats") if isinstance(payload.get("stats"), dict) else {},
        error=error,
    )


def parse_stream_json_result(result: CLIResult) -> CLIResult:
    events: list[dict[str, Any]] = []
    messages: list[str] = []
    stats: dict[str, Any] = {}
    session_id: str | None = None
    error: dict[str, Any] | None = None

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)

        event_type = event.get("type")
        if event_type == "init" and isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        elif event_type == "message" and event.get("role") == "assistant":
            content = event.get("content")
            if isinstance(content, str):
                messages.append(content)
        elif event_type == "result":
            if isinstance(event.get("stats"), dict):
                stats = event["stats"]
            if isinstance(event.get("error"), dict):
                error = event["error"]
        elif event_type == "error":
            error = {
                "type": str(event.get("severity") or "error"),
                "message": str(event.get("message") or ""),
            }

    output = "".join(messages).strip()
    if error and not output:
        output = str(error.get("message") or error)

    return replace(
        result,
        output=output or clean_terminal_output(result.stdout or result.raw_output),
        session_id=session_id,
        stats=stats,
        events=events,
        error=error,
    )


def _load_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def looks_like_auth_prompt(text: str) -> bool:
    return any(marker in text for marker in AUTH_PROMPT_MARKERS)
