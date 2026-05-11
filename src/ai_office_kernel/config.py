from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_ints(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            result.add(int(item))
    return result


def _csv_strings(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int) -> int:
    if not value:
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    allowed_chat_ids: set[int]
    workspace_root: Path
    prompt_dir: Path
    memory_messages: int
    qa_enabled: bool
    ollama_base_url: str
    router_model: str
    developer_backend: str
    qa_backend: str
    local_coder_model: str
    local_qa_model: str
    gemini_command: str
    gemini_model: str
    gemini_output_format: str
    gemini_approval_mode: str
    gemini_skip_trust: bool
    gemini_sandbox: bool
    gemini_all_files: bool
    gemini_include_directories: tuple[str, ...]
    gemini_allowed_tools: tuple[str, ...]
    gemini_resume: str | None
    cli_timeout_seconds: int
    progress_first_seconds: int
    progress_interval_seconds: int
    auto_confirm_cli: bool
    show_usage: bool
    voice_enabled: bool
    whisper_model: str
    tts_voice: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            allowed_chat_ids=_csv_ints(os.getenv("AI_OFFICE_ALLOWED_CHAT_IDS")),
            workspace_root=Path(
                os.getenv("AI_OFFICE_WORKSPACE_ROOT", os.getcwd())
            ).resolve(),
            prompt_dir=Path(
                os.getenv("AI_OFFICE_PROMPT_DIR", str(Path.cwd() / "prompts"))
            ).resolve(),
            memory_messages=_int(os.getenv("AI_OFFICE_MEMORY_MESSAGES"), 30),
            qa_enabled=_bool(os.getenv("AI_OFFICE_QA_ENABLED"), False),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            router_model=os.getenv("AI_OFFICE_ROUTER_MODEL", "qwen3:8b"),
            developer_backend=os.getenv("AI_OFFICE_DEVELOPER_BACKEND", "gemini"),
            qa_backend=os.getenv("AI_OFFICE_QA_BACKEND", "gemini"),
            local_coder_model=os.getenv(
                "AI_OFFICE_LOCAL_CODER_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"
            ),
            local_qa_model=os.getenv(
                "AI_OFFICE_LOCAL_QA_MODEL", "qwen3:8b"
            ),
            gemini_command=os.getenv("AI_OFFICE_GEMINI_CMD", "gemini"),
            gemini_model=os.getenv("AI_OFFICE_GEMINI_MODEL", "auto"),
            gemini_output_format=os.getenv("AI_OFFICE_GEMINI_OUTPUT_FORMAT", "json"),
            gemini_approval_mode=os.getenv(
                "AI_OFFICE_GEMINI_APPROVAL_MODE", "auto_edit"
            ),
            gemini_skip_trust=_bool(os.getenv("AI_OFFICE_GEMINI_SKIP_TRUST"), True),
            gemini_sandbox=_bool(os.getenv("AI_OFFICE_GEMINI_SANDBOX"), False),
            gemini_all_files=_bool(os.getenv("AI_OFFICE_GEMINI_ALL_FILES"), False),
            gemini_include_directories=_csv_strings(
                os.getenv("AI_OFFICE_GEMINI_INCLUDE_DIRECTORIES")
            ),
            gemini_allowed_tools=_csv_strings(
                os.getenv("AI_OFFICE_GEMINI_ALLOWED_TOOLS")
            ),
            gemini_resume=os.getenv("AI_OFFICE_GEMINI_RESUME") or None,
            cli_timeout_seconds=_int(os.getenv("AI_OFFICE_CLI_TIMEOUT_SECONDS"), 1200),
            progress_first_seconds=_int(os.getenv("AI_OFFICE_PROGRESS_FIRST_SECONDS"), 45),
            progress_interval_seconds=_int(
                os.getenv("AI_OFFICE_PROGRESS_INTERVAL_SECONDS"), 60
            ),
            auto_confirm_cli=_bool(os.getenv("AI_OFFICE_AUTO_CONFIRM_CLI"), False),
            show_usage=_bool(os.getenv("AI_OFFICE_SHOW_USAGE"), True),
            voice_enabled=_bool(os.getenv("AI_OFFICE_VOICE_ENABLED"), True),
            whisper_model=os.getenv("AI_OFFICE_WHISPER_MODEL", "base"),
            tts_voice=os.getenv("AI_OFFICE_TTS_VOICE", "ru-RU-SvetlanaNeural"),
        )
