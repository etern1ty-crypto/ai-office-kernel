from __future__ import annotations

import argparse
from dataclasses import replace

from ai_office_kernel.agent_loop import SecretaryAgentLoop
from ai_office_kernel.api_server import run_api_server
from ai_office_kernel.config import Settings
from ai_office_kernel.doctor import run_doctor
from ai_office_kernel.factory import build_orchestrator
from pathlib import Path

from ai_office_kernel.installer import SetupOptions, run_setup
from ai_office_kernel.local import OllamaClient
from ai_office_kernel.router import Router
from ai_office_kernel.telegram_gateway import TelegramGateway
from ai_office_kernel.tools import ToolRuntime, WorkspaceToolRunner


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai-office-kernel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    route_parser = subparsers.add_parser("route", help="print routing decision")
    route_parser.add_argument("text")

    ask_parser = subparsers.add_parser("ask", help="handle one text message")
    ask_parser.add_argument("text")
    ask_parser.add_argument("--chat-id", type=int, default=0)
    ask_parser.add_argument("--user-id", type=int, default=None)
    ask_parser.add_argument("--no-qa", action="store_true", help="disable QA pass for this request")
    ask_parser.add_argument("--developer-backend", choices=("gemini", "local"))
    ask_parser.add_argument("--qa-backend", choices=("gemini", "local"))
    ask_parser.add_argument("--timeout", type=int, help="Gemini CLI timeout seconds for this request")

    agent_parser = subparsers.add_parser("agent", help="talk to the agentic local secretary")
    agent_parser.add_argument("text")
    agent_parser.add_argument("--chat-id", type=int, default=0)
    agent_parser.add_argument("--user-id", type=int, default=None)
    agent_parser.add_argument("--verbose-tools", action="store_true", help="print successful tool events")

    api_parser = subparsers.add_parser("api", help="run minimal HTTP API for the agentic secretary")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=8787)

    subparsers.add_parser("telegram", help="run Telegram gateway")
    subparsers.add_parser("unload", help="unload configured Ollama model")
    doctor_parser = subparsers.add_parser("doctor", help="check runtime dependencies")
    doctor_parser.add_argument("--gemini-smoke", action="store_true", help="run a real Gemini CLI request")
    doctor_parser.add_argument("--strict", action="store_true", help="return non-zero on warnings")
    setup_parser = subparsers.add_parser("setup", help="installer/bootstrap")
    setup_parser.add_argument("--auto", action="store_true", help="run with defaults and install automatically")
    setup_parser.add_argument("--skip-python-install", action="store_true")
    setup_parser.add_argument("--skip-gemini-install", action="store_true")
    setup_parser.add_argument("--skip-ollama-install", action="store_true")
    setup_parser.add_argument("--skip-model-pull", action="store_true")
    setup_parser.add_argument("--skip-gemini-auth", action="store_true")
    setup_parser.add_argument("--gemini-auth", action="store_true", help="run Gemini CLI auth helper")
    setup_parser.add_argument("--telegram-bot-token")
    setup_parser.add_argument("--telegram-chat-id")
    setup_parser.add_argument("--workspace-root", type=Path)
    setup_parser.add_argument("--router-model")
    setup_parser.add_argument("--developer-backend", choices=("gemini", "local"))
    setup_parser.add_argument("--qa-backend", choices=("gemini", "local"))
    setup_parser.add_argument("--local-coder-model")
    setup_parser.add_argument("--local-qa-model")
    setup_parser.add_argument("--no-qa", action="store_true")
    setup_parser.add_argument("--gemini-model")
    setup_parser.add_argument("--gemini-approval-mode", choices=("auto_edit", "yolo", "default", "plan"))
    setup_parser.add_argument("--gemini-output-format", choices=("json", "stream-json", "text"))
    setup_parser.add_argument("--gemini-sandbox", action="store_true")

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "route":
        decision = Router().route(args.text)
        print(f"role={decision.role_id}")
        print(f"reason={decision.reason}")
        print(f"run_qa={decision.run_qa}")
        print(f"task={decision.task_text}")
        return

    if args.command == "unload":
        OllamaClient(
            base_url=settings.ollama_base_url,
            default_model=settings.router_model,
        ).unload_model()
        print(f"unloaded {settings.router_model}")
        return

    if args.command == "doctor":
        raise SystemExit(
            run_doctor(
                settings,
                gemini_smoke=args.gemini_smoke,
                strict=args.strict,
            )
        )

    if args.command == "setup":
        run_setup(
            options=SetupOptions(
                auto=args.auto,
                install_python=False if args.skip_python_install else None,
                install_gemini=False if args.skip_gemini_install else None,
                install_ollama=False if args.skip_ollama_install else None,
                pull_models=False if args.skip_model_pull else None,
                run_gemini_auth=True
                if args.gemini_auth
                else (False if args.skip_gemini_auth else None),
                telegram_bot_token=args.telegram_bot_token,
                telegram_chat_id=args.telegram_chat_id,
                workspace_root=args.workspace_root,
                router_model=args.router_model,
                developer_backend=args.developer_backend,
                qa_backend=args.qa_backend,
                local_coder_model=args.local_coder_model,
                local_qa_model=args.local_qa_model,
                qa_enabled=False if args.no_qa else None,
                gemini_model=args.gemini_model,
                gemini_approval_mode=args.gemini_approval_mode,
                gemini_output_format=args.gemini_output_format,
                gemini_sandbox=True if args.gemini_sandbox else None,
            )
        )
        return

    orchestrator = build_orchestrator(settings)
    if args.command == "ask":
        if args.no_qa or args.developer_backend or args.qa_backend or args.timeout:
            settings = replace(
                settings,
                qa_enabled=False if args.no_qa else settings.qa_enabled,
                developer_backend=args.developer_backend or settings.developer_backend,
                qa_backend=args.qa_backend or settings.qa_backend,
                cli_timeout_seconds=args.timeout or settings.cli_timeout_seconds,
            )
            orchestrator = build_orchestrator(settings)
        response = orchestrator.handle_text(args.chat_id, args.user_id, args.text)
        print(f"{response.prefix}: {response.text}")
        return

    if args.command == "agent":
        agent_loop = _build_agent_loop(orchestrator)
        result = agent_loop.run(args.chat_id, args.user_id, args.text)
        for event in result.events:
            if not args.verbose_tools and event.kind not in {"status", "tool_error", "need_confirm"}:
                continue
            print(f"[{event.kind}] {event.label}")
            if event.content and event.kind == "tool_error":
                print(event.content)
        if result.pending is not None:
            print()
            print("[pending_confirmation]")
            print(f"tool={result.pending.request.name}")
            print(f"risk={result.pending.request.risk}")
            print(f"args={result.pending.request.args}")
        print()
        print(result.text)
        return

    if args.command == "api":
        run_api_server(
            _build_agent_loop(orchestrator),
            host=args.host,
            port=args.port,
        )
        return

    if args.command == "telegram":
        if not settings.telegram_bot_token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required")
        TelegramGateway(
            orchestrator,
            settings.telegram_bot_token,
            settings.allowed_chat_ids,
        ).run()


def _build_agent_loop(orchestrator):
    tool_runner = WorkspaceToolRunner(orchestrator.settings.workspace_root)

    def runner_for(*, developer_backend: str | None = None):
        settings = orchestrator.settings
        if developer_backend is not None:
            settings = replace(settings, developer_backend=developer_backend, qa_enabled=False)
        runner = build_orchestrator(settings)
        runner.memory = orchestrator.memory
        runner.router = orchestrator.router
        runner.ollama = orchestrator.ollama
        return runner

    def tool_runtime(chat_id: int) -> ToolRuntime:
        return ToolRuntime(
            orchestrator.settings.workspace_root,
            workspace=tool_runner,
            ask_local_coder=lambda prompt: runner_for(developer_backend="local")
            .handle_text(chat_id, None, "@dev " + prompt)
            .text,
            ask_gemini_cli=lambda prompt: runner_for(developer_backend="gemini")
            .handle_text(chat_id, None, "@dev " + prompt)
            .text,
            request_gemini_model_change=lambda model: (
                "CLI/API mode cannot apply per-chat Gemini model changes. "
                "Set AI_OFFICE_GEMINI_MODEL in .env or use Telegram /model with confirmation."
            ),
        )

    return SecretaryAgentLoop(
        orchestrator.settings,
        ollama=orchestrator.ollama,
        memory=orchestrator.memory,
        tool_runtime_factory=tool_runtime,
    )


if __name__ == "__main__":
    main()
