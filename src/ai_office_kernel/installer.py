from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path

from ai_office_kernel.roles import DEFAULT_ROLES


GEMINI_PACKAGE = "@google/gemini-cli"
DEFAULT_SECRETARY_MODEL = "qwen3:8b"
DEFAULT_CODER_MODEL = "qwen2.5-coder:7b-instruct-q4_K_M"
DEFAULT_QA_MODEL = "qwen3:8b"
DEFAULT_GEMINI_MODEL = "auto"

MODEL_PRESETS = (
    ("qwen3:8b", "agentic secretary/router with tool calling, ~8B"),
    ("llama3.1:8b-instruct-q4_K_M", "balanced fallback secretary/router, ~8B Q4"),
    ("phi3.5:latest", "lighter secretary/router"),
    ("qwen2.5-coder:7b-instruct-q4_K_M", "local coding model, ~7B Q4"),
    ("custom", "custom Ollama tag or Hugging Face GGUF URL"),
)


@dataclass(frozen=True)
class InstallerConfig:
    telegram_bot_token: str
    telegram_chat_id: str
    workspace_root: Path
    router_model: str
    developer_backend: str
    qa_backend: str
    local_coder_model: str
    local_qa_model: str
    gemini_model: str
    gemini_approval_mode: str
    gemini_output_format: str
    gemini_skip_trust: bool
    gemini_sandbox: bool
    qa_enabled: bool


@dataclass(frozen=True)
class SetupOptions:
    auto: bool = False
    install_python: bool | None = None
    install_gemini: bool | None = None
    install_ollama: bool | None = None
    pull_models: bool | None = None
    run_gemini_auth: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    workspace_root: Path | None = None
    router_model: str | None = None
    developer_backend: str | None = None
    qa_backend: str | None = None
    local_coder_model: str | None = None
    local_qa_model: str | None = None
    qa_enabled: bool | None = None
    gemini_model: str | None = None
    gemini_approval_mode: str | None = None
    gemini_output_format: str | None = None
    gemini_skip_trust: bool | None = None
    gemini_sandbox: bool | None = None


def run_setup(project_root: Path | None = None, options: SetupOptions | None = None) -> None:
    options = options or SetupOptions()
    root = project_root or Path.cwd()
    print("AI-Office Kernel setup")
    print(f"Project root: {root}")
    print()

    if options.install_python is False:
        print("Python dependency install: skipped")
    else:
        install_python_dependencies(
            root,
            auto=options.auto or options.install_python is True,
        )

    install_nodejs_if_missing(auto=options.auto)

    if options.install_gemini is False:
        print("Gemini CLI install: skipped")
    else:
        install_gemini_cli(auto=options.auto or options.install_gemini is True)

    if options.install_ollama is False:
        print("Ollama install: skipped")
        ollama_ready = shutil.which("ollama") is not None
    else:
        ollama_ready = install_or_check_ollama(
            auto=options.auto or options.install_ollama is True
        )

    telegram_bot_token = _value_or_prompt(
        options.telegram_bot_token,
        "Telegram bot token (empty to skip)",
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        auto=options.auto,
        secret=True,
    )
    telegram_chat_id = _value_or_prompt(
        options.telegram_chat_id,
        "Telegram chat id for installer/auth links (empty to skip)",
        os.getenv("AI_OFFICE_ALLOWED_CHAT_IDS", "").split(",")[0] or "",
        auto=options.auto,
    )
    workspace_root = (
        options.workspace_root
        or Path(
            _value_or_prompt(
                None,
                "Workspace root",
                os.getenv("AI_OFFICE_WORKSPACE_ROOT", str(root.parent)),
                auto=options.auto,
            )
        ).expanduser()
    )

    pull_models = _flag(options.pull_models, True) and ollama_ready
    if not ollama_ready:
        print("Ollama is not available. Model pulls are skipped for this run.")

    router_model = configure_ollama_model(
        "Secretary/router local model",
        default=options.router_model
        or os.getenv("AI_OFFICE_ROUTER_MODEL", DEFAULT_SECRETARY_MODEL),
        install_prompt="Pull/create this model in Ollama now?",
        auto=options.auto,
        pull=pull_models,
    )

    developer_backend = _choice_or_prompt(
        options.developer_backend,
        "Developer backend",
        choices=("gemini", "local"),
        default=os.getenv("AI_OFFICE_DEVELOPER_BACKEND", "gemini"),
        auto=options.auto,
    )
    local_coder_model = configure_ollama_model(
        "Local coder model",
        default=options.local_coder_model
        or os.getenv("AI_OFFICE_LOCAL_CODER_MODEL", DEFAULT_CODER_MODEL),
        install_prompt="Pull/create local coder model in Ollama now?",
        auto=options.auto,
        pull=pull_models,
    )

    qa_enabled = (
        options.qa_enabled
        if options.qa_enabled is not None
        else _bool_or_prompt(
            "Enable QA pass before Telegram answer?",
            default=os.getenv("AI_OFFICE_QA_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            auto=options.auto,
        )
    )
    qa_backend = _choice_or_prompt(
        options.qa_backend,
        "QA backend",
        choices=("gemini", "local"),
        default=os.getenv("AI_OFFICE_QA_BACKEND", "gemini"),
        auto=options.auto,
    )
    local_qa_model = configure_ollama_model(
        "Local QA model",
        default=options.local_qa_model
        or os.getenv("AI_OFFICE_LOCAL_QA_MODEL", DEFAULT_QA_MODEL),
        install_prompt="Pull/create local QA model in Ollama now?",
        auto=options.auto,
        pull=pull_models,
    )

    config = InstallerConfig(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        workspace_root=workspace_root.resolve(),
        router_model=router_model,
        developer_backend=developer_backend,
        qa_backend=qa_backend,
        local_coder_model=local_coder_model,
        local_qa_model=local_qa_model,
        gemini_model=_value_or_prompt(
            options.gemini_model,
            "Gemini model",
            os.getenv("AI_OFFICE_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            auto=options.auto,
        ),
        gemini_approval_mode=_choice_or_prompt(
            options.gemini_approval_mode,
            "Gemini approval mode",
            choices=("auto_edit", "yolo", "default", "plan"),
            default=os.getenv("AI_OFFICE_GEMINI_APPROVAL_MODE", "auto_edit"),
            auto=options.auto,
        ),
        gemini_output_format=_choice_or_prompt(
            options.gemini_output_format,
            "Gemini output format",
            choices=("json", "stream-json", "text"),
            default=os.getenv("AI_OFFICE_GEMINI_OUTPUT_FORMAT", "json"),
            auto=options.auto,
        ),
        gemini_skip_trust=(
            options.gemini_skip_trust
            if options.gemini_skip_trust is not None
            else _bool_or_prompt(
                "Use --skip-trust for Gemini CLI service runs?",
                default=os.getenv("AI_OFFICE_GEMINI_SKIP_TRUST", "true").lower()
                in {"1", "true", "yes"},
                auto=options.auto,
            )
        ),
        gemini_sandbox=(
            options.gemini_sandbox
            if options.gemini_sandbox is not None
            else _bool_or_prompt(
                "Use --sandbox for Gemini CLI runs?",
                default=os.getenv("AI_OFFICE_GEMINI_SANDBOX", "false").lower()
                in {"1", "true", "yes"},
                auto=options.auto,
            )
        ),
        qa_enabled=qa_enabled,
    )

    write_env(root / ".env", config)
    write_default_prompts(root)
    write_gemini_service_settings(root)
    if workspace_root.resolve() != root.resolve():
        write_gemini_service_settings(workspace_root)

    should_auth = _flag(options.run_gemini_auth, False)
    if should_auth:
        if options.auto:
            print("Gemini auth will start automatically. Browser login still requires user confirmation.")
        elif not prompt_bool("Run Gemini CLI browser authentication now?", default=False):
            should_auth = False
    if should_auth:
        run_gemini_auth(
            command="gemini",
            telegram_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
        )
    else:
        print_manual_gemini_auth()

    print()
    print("Setup complete.")
    print("Run: ai-office-kernel telegram")


def run_setup_interactive_legacy(project_root: Path | None = None) -> None:
    root = project_root or Path.cwd()
    print("AI-Office Kernel setup")
    print(f"Project root: {root}")
    print()

    install_python_dependencies(root, auto=False)
    install_nodejs_if_missing(auto=False)
    install_gemini_cli(auto=False)
    ollama_ready = install_or_check_ollama(auto=False)

    telegram_bot_token = prompt_text(
        "Telegram bot token (empty to skip)",
        default=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        secret=True,
    )
    telegram_chat_id = prompt_text(
        "Telegram chat id for installer/auth links (empty to skip)",
        default=(os.getenv("AI_OFFICE_ALLOWED_CHAT_IDS", "").split(",")[0] or ""),
    )
    workspace_root = Path(
        prompt_text(
            "Workspace root",
            default=os.getenv("AI_OFFICE_WORKSPACE_ROOT", str(root.parent)),
        )
    ).expanduser()

    router_model = configure_ollama_model(
        "Secretary/router local model",
        default=os.getenv("AI_OFFICE_ROUTER_MODEL", DEFAULT_SECRETARY_MODEL),
        install_prompt="Pull/create this model in Ollama now?",
        auto=False,
        pull=ollama_ready,
    )

    developer_backend = prompt_choice(
        "Developer backend",
        choices=("gemini", "local"),
        default=os.getenv("AI_OFFICE_DEVELOPER_BACKEND", "gemini"),
    )
    local_coder_model = configure_ollama_model(
        "Local coder model",
        default=os.getenv("AI_OFFICE_LOCAL_CODER_MODEL", DEFAULT_CODER_MODEL),
        install_prompt="Pull/create local coder model in Ollama now?",
        auto=False,
        pull=ollama_ready,
    )

    qa_enabled = prompt_bool(
        "Enable QA pass before Telegram answer?",
            default=os.getenv("AI_OFFICE_QA_ENABLED", "false").lower() in {"1", "true", "yes"},
    )
    qa_backend = prompt_choice(
        "QA backend",
        choices=("gemini", "local"),
        default=os.getenv("AI_OFFICE_QA_BACKEND", "gemini"),
    )
    local_qa_model = configure_ollama_model(
        "Local QA model",
        default=os.getenv("AI_OFFICE_LOCAL_QA_MODEL", DEFAULT_QA_MODEL),
        install_prompt="Pull/create local QA model in Ollama now?",
        auto=False,
        pull=ollama_ready,
    )

    config = InstallerConfig(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        workspace_root=workspace_root.resolve(),
        router_model=router_model,
        developer_backend=developer_backend,
        qa_backend=qa_backend,
        local_coder_model=local_coder_model,
        local_qa_model=local_qa_model,
        gemini_model=prompt_text(
            "Gemini model",
            default=os.getenv("AI_OFFICE_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        ),
        gemini_approval_mode=prompt_choice(
            "Gemini approval mode",
            choices=("auto_edit", "yolo", "default", "plan"),
            default=os.getenv("AI_OFFICE_GEMINI_APPROVAL_MODE", "auto_edit"),
        ),
        gemini_output_format=prompt_choice(
            "Gemini output format",
            choices=("json", "stream-json", "text"),
            default=os.getenv("AI_OFFICE_GEMINI_OUTPUT_FORMAT", "json"),
        ),
        gemini_skip_trust=prompt_bool(
            "Use --skip-trust for Gemini CLI service runs?",
            default=os.getenv("AI_OFFICE_GEMINI_SKIP_TRUST", "true").lower()
            in {"1", "true", "yes"},
        ),
        gemini_sandbox=prompt_bool(
            "Use --sandbox for Gemini CLI runs?",
            default=os.getenv("AI_OFFICE_GEMINI_SANDBOX", "false").lower()
            in {"1", "true", "yes"},
        ),
        qa_enabled=qa_enabled,
    )

    write_env(root / ".env", config)
    write_default_prompts(root)
    write_gemini_service_settings(root)
    if workspace_root.resolve() != root.resolve():
        write_gemini_service_settings(workspace_root)

    if prompt_bool("Run Gemini CLI browser authentication now?", default=False):
        run_gemini_auth(
            command="gemini",
            telegram_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
        )
    else:
        print_manual_gemini_auth()

    print()
    print("Setup complete.")
    print("Run: ai-office-kernel telegram")


def install_python_dependencies(project_root: Path, *, auto: bool) -> None:
    if not (project_root / "pyproject.toml").exists():
        return
    if auto or prompt_bool("Install Python package and dependencies with pip?", default=False):
        run_command([sys.executable, "-m", "pip", "install", "-e", "."], cwd=project_root)


def install_nodejs_if_missing(*, auto: bool) -> bool:
    if shutil.which("npm"):
        print("npm: found")
        return True
    print("npm: not found")
    if not shutil.which("apt-get"):
        print("Cannot auto-install Node.js/npm: apt-get is not available.")
        return False
    if auto or prompt_bool("Install Node.js/npm with apt-get?", default=False):
        run_command(["sudo", "apt-get", "update"])
        run_command(["sudo", "apt-get", "install", "-y", "nodejs", "npm"])
    return shutil.which("npm") is not None


def install_gemini_cli(*, auto: bool) -> bool:
    if shutil.which("gemini"):
        print("Gemini CLI: found")
        return True
    print("Gemini CLI: not found")
    if not shutil.which("npm"):
        print("npm is not installed. Install Node.js 20+ first, then rerun setup.")
        return False
    if auto or prompt_bool(f"Install Gemini CLI globally with npm ({GEMINI_PACKAGE})?", default=True):
        run_command(["npm", "install", "-g", GEMINI_PACKAGE])
    return shutil.which("gemini") is not None


def install_or_check_ollama(*, auto: bool) -> bool:
    if shutil.which("ollama"):
        print("Ollama: found")
        ensure_ollama_server()
        return True
    print("Ollama: not found")
    if not (auto or prompt_bool("Install Ollama using the official Linux install script?", default=False)):
        print("Skipping Ollama install. Local agents will work after Ollama is installed.")
        return False

    ensure_linux_package("zstd", auto=True)
    ensure_linux_package("ripgrep", auto=True)

    url = "https://ollama.com/install.sh"
    with tempfile.TemporaryDirectory() as tempdir:
        script_path = Path(tempdir) / "ollama-install.sh"
        download_file(url, script_path)
        exit_code = run_command(["sh", str(script_path)])
        if exit_code != 0:
            if not shutil.which("zstd"):
                print("Ollama install failed and zstd is still missing.")
            print("Ollama install failed. Fix the system dependency above and rerun setup.")
            return False
    if not shutil.which("ollama"):
        print("Ollama binary is still not in PATH after install.")
        return False
    ensure_ollama_server()
    return shutil.which("ollama") is not None


def ensure_linux_package(command_name: str, *, auto: bool) -> bool:
    if shutil.which(command_name):
        print(f"{command_name}: found")
        return True
    if not shutil.which("apt-get"):
        print(f"{command_name}: missing, and apt-get is not available.")
        return False
    if auto or prompt_bool(f"Install system package {command_name} with apt-get?", default=True):
        run_command(["sudo", "apt-get", "update"])
        run_command(["sudo", "apt-get", "install", "-y", command_name])
    if not shutil.which(command_name):
        print(f"{command_name}: still missing.")
        return False
    print(f"{command_name}: installed")
    return True


def configure_ollama_model(
    label: str,
    default: str,
    install_prompt: str,
    *,
    auto: bool,
    pull: bool,
) -> str:
    if auto:
        value = default
        print(f"{label}: {value}")
        if looks_like_url(value):
            model_name = safe_model_name(value)
            if pull:
                gguf_path = download_hf_gguf(value, model_name)
                create_ollama_model(model_name, gguf_path)
            return model_name
        if pull:
            pull_ollama_model(value)
        return value

    print()
    print(label)
    for idx, (name, description) in enumerate(MODEL_PRESETS, start=1):
        marker = " (default)" if name == default else ""
        print(f"  {idx}. {name}{marker} - {description}")
    value = prompt_text("Model tag, preset number, or Hugging Face GGUF URL", default=default)

    if value.isdigit() and 1 <= int(value) <= len(MODEL_PRESETS):
        selected = MODEL_PRESETS[int(value) - 1][0]
        value = prompt_text("Custom model tag or GGUF URL", default=default) if selected == "custom" else selected

    if looks_like_url(value):
        model_name = prompt_text(
            "Ollama model name to create from GGUF",
            default=safe_model_name(value),
        )
        if pull and prompt_bool(install_prompt, default=True):
            gguf_path = download_hf_gguf(value, model_name)
            create_ollama_model(model_name, gguf_path)
        return model_name

    if pull and prompt_bool(install_prompt, default=True):
        pull_ollama_model(value)
    return value


def pull_ollama_model(model: str) -> None:
    if not shutil.which("ollama"):
        print(f"Cannot pull {model}: ollama is not installed.")
        return
    ensure_ollama_server()
    run_command(["ollama", "pull", model])


def download_hf_gguf(url: str, model_name: str) -> Path:
    models_dir = Path.cwd() / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    filename = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
    if not filename.lower().endswith(".gguf"):
        filename = f"{model_name}.gguf"
    target = models_dir / filename
    download_file(url, target)
    return target.resolve()


def create_ollama_model(model_name: str, gguf_path: Path) -> None:
    if not shutil.which("ollama"):
        print(f"Cannot create {model_name}: ollama is not installed.")
        return
    ensure_ollama_server()
    modelfile = Path.cwd() / "models" / f"{model_name.replace(':', '_')}.Modelfile"
    modelfile.write_text(
        "\n".join(
            [
                f"FROM {gguf_path}",
                "PARAMETER temperature 0.2",
                'SYSTEM """You are a concise local AI office agent."""',
                "",
            ]
        ),
        encoding="utf-8",
    )
    run_command(["ollama", "create", model_name, "-f", str(modelfile)])


def ensure_ollama_server() -> None:
    if not shutil.which("ollama"):
        return
    if _ollama_responds():
        print("Ollama server: running")
        return

    state_dir = Path.cwd() / ".ai-office"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "ollama.log"
    print(f"Starting Ollama server in background, log: {log_path}")
    log_file = log_path.open("ab")
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(20):
        time.sleep(0.5)
        if _ollama_responds():
            print("Ollama server: running")
            return
    print("Ollama server did not respond yet. Check .ai-office/ollama.log")


def _ollama_responds() -> bool:
    try:
        completed = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def run_gemini_auth(command: str, telegram_token: str, telegram_chat_id: str) -> None:
    if not shutil.which(command):
        print("Gemini CLI is not installed; skipping auth.")
        return

    print()
    print("Starting Gemini CLI auth. Choose 'Login with Google' if prompted.")
    print("No tokens are read by this installer; Gemini CLI caches credentials itself.")

    try:
        import pexpect
    except ImportError:
        print("pexpect is unavailable. Starting Gemini CLI directly.")
        subprocess.run([command], env={**os.environ, "NO_BROWSER": "1"}, check=False)
        input("After completing auth, press Enter to continue...")
        return

    env = {**os.environ, "NO_BROWSER": "1"}
    child = pexpect.spawn(command, env=env, encoding="utf-8", timeout=60)
    url_sent = False
    login_selected = False
    url_re = re.compile(r"https?://[^\s<>()\]\}]+")

    try:
        while True:
            index = child.expect(
                [
                    url_re,
                    "Login with Google",
                    "How would you like to authenticate",
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ]
            )
            output = strip_ansi(_pexpect_text(child.before) + _pexpect_text(child.after))
            print(output, end="" if output.endswith("\n") else "\n")

            match = url_re.search(output)
            if match and not url_sent:
                auth_url = match.group(0)
                print()
                print(f"Gemini auth URL: {auth_url}")
                send_telegram_message(
                    telegram_token,
                    telegram_chat_id,
                    f"Gemini CLI auth URL:\n{auth_url}",
                )
                url_sent = True

            if index in (1, 2) and not login_selected:
                child.sendline("1")
                login_selected = True
                continue
            if index == 3:
                break
            if index == 4:
                if url_sent:
                    break
                print("Waiting for Gemini CLI auth output...")
    except KeyboardInterrupt:
        print("\nGemini auth interrupted. No project files were changed by auth.")
    finally:
        try:
            input("After completing auth in the browser, press Enter here...")
        except KeyboardInterrupt:
            print()
        if child.isalive():
            child.sendline("/quit")
            try:
                child.expect(pexpect.EOF, timeout=10)
            except pexpect.TIMEOUT:
                child.terminate(force=True)


def print_manual_gemini_auth() -> None:
    print()
    print("Gemini CLI auth is manual by default.")
    print("Run this when you are ready:")
    print("  NO_BROWSER=1 gemini")
    print("Open the printed URL, finish Google login, then return to the terminal.")
    print("Quick check after login:")
    print("  cd /tmp")
    print(
        '  gemini --prompt "Reply with exactly OK. Do not inspect files. Do not use tools." '
        "--output-format json --approval-mode default --skip-trust"
    )


def write_env(path: Path, config: InstallerConfig, *, quiet: bool = False) -> None:
    lines = [
        f"TELEGRAM_BOT_TOKEN={config.telegram_bot_token}",
        f"AI_OFFICE_ALLOWED_CHAT_IDS={config.telegram_chat_id}",
        f"AI_OFFICE_WORKSPACE_ROOT={config.workspace_root}",
        f"AI_OFFICE_PROMPT_DIR={path.parent / 'prompts'}",
        "AI_OFFICE_MEMORY_MESSAGES=30",
        f"AI_OFFICE_QA_ENABLED={str(config.qa_enabled).lower()}",
        "OLLAMA_BASE_URL=http://127.0.0.1:11434",
        f"AI_OFFICE_ROUTER_MODEL={config.router_model}",
        f"AI_OFFICE_DEVELOPER_BACKEND={config.developer_backend}",
        f"AI_OFFICE_QA_BACKEND={config.qa_backend}",
        f"AI_OFFICE_LOCAL_CODER_MODEL={config.local_coder_model}",
        f"AI_OFFICE_LOCAL_QA_MODEL={config.local_qa_model}",
        "AI_OFFICE_GEMINI_CMD=gemini",
        f"AI_OFFICE_GEMINI_MODEL={config.gemini_model}",
        f"AI_OFFICE_GEMINI_OUTPUT_FORMAT={config.gemini_output_format}",
        f"AI_OFFICE_GEMINI_APPROVAL_MODE={config.gemini_approval_mode}",
        f"AI_OFFICE_GEMINI_SKIP_TRUST={str(config.gemini_skip_trust).lower()}",
        f"AI_OFFICE_GEMINI_SANDBOX={str(config.gemini_sandbox).lower()}",
        "AI_OFFICE_GEMINI_ALL_FILES=false",
        "AI_OFFICE_GEMINI_INCLUDE_DIRECTORIES=",
        "AI_OFFICE_GEMINI_ALLOWED_TOOLS=",
        "AI_OFFICE_GEMINI_RESUME=",
        "AI_OFFICE_CLI_TIMEOUT_SECONDS=1200",
        "AI_OFFICE_PROGRESS_FIRST_SECONDS=45",
        "AI_OFFICE_PROGRESS_INTERVAL_SECONDS=60",
        "AI_OFFICE_AUTO_CONFIRM_CLI=false",
        "AI_OFFICE_SHOW_USAGE=true",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    if not quiet:
        print(f"Wrote {path}")


def write_default_prompts(project_root: Path, *, quiet: bool = False) -> Path:
    prompt_dir = project_root / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for role_id, role in DEFAULT_ROLES.items():
        path = prompt_dir / f"{role_id}.md"
        if path.exists():
            continue
        path.write_text(role.system_prompt.strip() + "\n", encoding="utf-8")
    if not quiet:
        print(f"Wrote default prompts in {prompt_dir}")
    return prompt_dir


def write_gemini_service_settings(project_root: Path, *, quiet: bool = False) -> Path:
    settings = {
        "experimental": {
            "enableAgents": False,
            "adk": {
                "agentSessionNoninteractiveEnabled": False,
                "agentSessionInteractiveEnabled": False,
            },
        },
        "tools": {
            "core": [
                "glob",
                "grep_search",
                "list_directory",
                "read_file",
                "read_many_files",
                "write_file",
                "replace",
            ],
            "exclude": [
                "run_shell_command",
                "ask_user",
                "invoke_agent",
            ],
        },
    }
    path = project_root / ".gemini" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    if not quiet:
        print(f"Wrote {path}")
    return path


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=20) as response:
            response.read()
        print("Auth URL sent to Telegram.")
    except Exception as exc:  # noqa: BLE001 - installer should not crash here.
        print(f"Could not send Telegram message: {exc}")


def download_file(url: str, target: Path) -> None:
    print(f"Downloading {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with target.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = int(downloaded * 100 / total)
                    print(f"\r{percent}% {downloaded // (1024 * 1024)} MiB", end="")
    print()
    print(f"Saved {target}")


def run_command(args: list[str], cwd: Path | None = None) -> int:
    print("$ " + " ".join(args))
    completed = subprocess.run(args, cwd=cwd, check=False)
    if completed.returncode:
        print(f"Command exited with code {completed.returncode}")
    return completed.returncode


def prompt_text(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default and not secret else ""
    reader = getpass if secret else input
    value = reader(f"{prompt}{suffix}: ").strip()
    return value or default


def prompt_bool(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "да", "д"}


def prompt_choice(prompt: str, choices: tuple[str, ...], default: str) -> str:
    normalized_default = default if default in choices else choices[0]
    joined = "/".join(choices)
    while True:
        value = input(f"{prompt} ({joined}) [{normalized_default}]: ").strip()
        value = value or normalized_default
        if value in choices:
            return value
        print(f"Choose one of: {joined}")


def _flag(value: bool | None, default: bool) -> bool:
    return default if value is None else value


def _value_or_prompt(
    value: str | None,
    prompt: str,
    default: str,
    *,
    auto: bool,
    secret: bool = False,
) -> str:
    if value is not None:
        return value
    if auto:
        return default
    return prompt_text(prompt, default=default, secret=secret)


def _bool_or_prompt(prompt: str, default: bool, *, auto: bool) -> bool:
    if auto:
        return default
    return prompt_bool(prompt, default=default)


def _choice_or_prompt(
    value: str | None,
    prompt: str,
    *,
    choices: tuple[str, ...],
    default: str,
    auto: bool,
) -> str:
    if value in choices:
        return value
    normalized_default = default if default in choices else choices[0]
    if auto:
        return normalized_default
    return prompt_choice(prompt, choices=choices, default=normalized_default)


def looks_like_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")


def safe_model_name(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    stem = Path(parsed.path).stem or "hf-local-model"
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", stem).strip("-")
    return safe.lower() or "hf-local-model"


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _pexpect_text(value: object) -> str:
    return value if isinstance(value, str) else ""
