from __future__ import annotations

from ai_office_kernel.cli import GeminiCLIAdapter
from ai_office_kernel.config import Settings
from ai_office_kernel.orchestrator import AgentOrchestrator
from ai_office_kernel.roles import role_prompt


def build_orchestrator(settings: Settings) -> AgentOrchestrator:
    developer = GeminiCLIAdapter(
        command=settings.gemini_command,
        system_prompt=role_prompt("developer", settings.prompt_dir),
        model=settings.gemini_model,
        output_format=settings.gemini_output_format,  # type: ignore[arg-type]
        approval_mode=settings.gemini_approval_mode,  # type: ignore[arg-type]
        skip_trust=settings.gemini_skip_trust,
        sandbox=settings.gemini_sandbox,
        all_files=settings.gemini_all_files,
        include_directories=settings.gemini_include_directories,
        allowed_tools=settings.gemini_allowed_tools,
        resume=settings.gemini_resume,
        timeout_seconds=settings.cli_timeout_seconds,
        auto_confirm=settings.auto_confirm_cli,
    )
    qa = GeminiCLIAdapter(
        command=settings.gemini_command,
        system_prompt=role_prompt("qa", settings.prompt_dir),
        model=settings.gemini_model,
        output_format=settings.gemini_output_format,  # type: ignore[arg-type]
        approval_mode=settings.gemini_approval_mode,  # type: ignore[arg-type]
        skip_trust=settings.gemini_skip_trust,
        sandbox=settings.gemini_sandbox,
        all_files=settings.gemini_all_files,
        include_directories=settings.gemini_include_directories,
        allowed_tools=settings.gemini_allowed_tools,
        resume=settings.gemini_resume,
        timeout_seconds=settings.cli_timeout_seconds,
        auto_confirm=settings.auto_confirm_cli,
    )
    return AgentOrchestrator(
        settings=settings,
        developer_adapter=developer,
        qa_adapter=qa,
    )
