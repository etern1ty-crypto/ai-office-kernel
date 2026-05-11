from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_office_kernel.config import Settings
from ai_office_kernel.local import OllamaClient, OllamaError


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def run_doctor(settings: Settings, *, gemini_smoke: bool = False, strict: bool = False) -> int:
    checks = collect_checks(settings, gemini_smoke=gemini_smoke)
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")

    failures = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    print()
    print(f"Summary: {len(failures)} fail, {len(warnings)} warn, {len(checks)} checks")
    if failures or (strict and warnings):
        return 1
    return 0


def collect_checks(settings: Settings, *, gemini_smoke: bool = False) -> list[Check]:
    checks: list[Check] = []
    checks.append(check_python())
    checks.append(check_env_file())
    checks.append(check_workspace(settings))
    checks.append(check_prompt_dir(settings))
    checks.extend(check_gemini_service_settings(settings))
    checks.append(check_ripgrep())
    checks.extend(check_gemini(settings, smoke=gemini_smoke))
    checks.extend(check_ollama(settings))
    checks.append(check_telegram(settings))
    return checks


def check_python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 11):
        return Check("python", "OK", text)
    return Check("python", "FAIL", f"{text}; Python 3.11+ is required")


def check_env_file() -> Check:
    path = Path.cwd() / ".env"
    if path.exists():
        return Check(".env", "OK", str(path))
    return Check(".env", "WARN", "not found in current directory; source env vars manually or run setup")


def check_workspace(settings: Settings) -> Check:
    if settings.workspace_root.exists():
        return Check("workspace", "OK", str(settings.workspace_root))
    return Check("workspace", "FAIL", f"missing: {settings.workspace_root}")


def check_prompt_dir(settings: Settings) -> Check:
    prompt_dir = settings.prompt_dir
    if not prompt_dir.exists():
        return Check("prompts", "WARN", f"missing: {prompt_dir}; setup will create defaults")
    missing = [
        role_id
        for role_id in ("secretary", "developer", "qa")
        if not (prompt_dir / f"{role_id}.md").exists()
    ]
    if missing:
        return Check("prompts", "WARN", f"{prompt_dir}; missing: {', '.join(missing)}")
    return Check("prompts", "OK", str(prompt_dir))


def check_gemini_service_settings(settings: Settings) -> list[Check]:
    paths = [Path.cwd() / ".gemini" / "settings.json"]
    workspace_path = settings.workspace_root / ".gemini" / "settings.json"
    if workspace_path not in paths:
        paths.append(workspace_path)

    checks: list[Check] = []
    for path in paths:
        name = "gemini service settings"
        if path == workspace_path:
            name = "gemini workspace settings"
        if not path.exists():
            checks.append(Check(name, "WARN", f"missing: {path}; rerun setup"))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            checks.append(Check(name, "FAIL", f"invalid JSON in {path}: {exc}"))
            continue
        enable_agents = payload.get("experimental", {}).get("enableAgents")
        excluded = set(payload.get("tools", {}).get("exclude", []))
        if enable_agents is False and "run_shell_command" in excluded:
            checks.append(Check(name, "OK", str(path)))
        else:
            checks.append(
                Check(
                    name,
                    "WARN",
                    f"{path} should disable experimental.enableAgents and exclude run_shell_command",
                )
            )
    return checks


def check_ripgrep() -> Check:
    path = shutil.which("rg")
    if path:
        return Check("ripgrep", "OK", path)
    return Check("ripgrep", "WARN", "rg not found; install with: sudo apt-get install -y ripgrep")


def check_gemini(settings: Settings, *, smoke: bool) -> list[Check]:
    checks: list[Check] = []
    path = shutil.which(settings.gemini_command)
    needs_gemini = settings.developer_backend == "gemini" or settings.qa_backend == "gemini"
    if not path:
        status = "FAIL" if needs_gemini else "WARN"
        return [Check("gemini cli", status, f"{settings.gemini_command!r} not found in PATH")]

    version = run_capture([settings.gemini_command, "--version"], timeout=15)
    if version.returncode == 0:
        checks.append(Check("gemini cli", "OK", f"{path}; version {version.stdout.strip()}"))
    else:
        checks.append(Check("gemini cli", "WARN", f"{path}; version check failed: {version.stderr.strip()}"))

    if smoke:
        result = run_capture(
            [
                settings.gemini_command,
                "-p",
                "Reply with exactly OK. Do not inspect files. Do not use tools.",
                "-o",
                "json",
                "--approval-mode=default",
                "--skip-trust",
            ],
            cwd=Path("/tmp"),
            timeout=max(settings.cli_timeout_seconds, 90),
        )
        if result.returncode != 0:
            checks.append(
                Check(
                    "gemini smoke",
                    "FAIL",
                    (result.stderr or result.stdout).strip()[:500],
                )
            )
        else:
            payload = parse_json_object(result.stdout)
            response = str((payload or {}).get("response", "")).strip()
            if response:
                checks.append(Check("gemini smoke", "OK", response[:120]))
            else:
                checks.append(Check("gemini smoke", "WARN", result.stdout.strip()[:500]))
    return checks


def check_ollama(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    path = shutil.which("ollama")
    if not path:
        return [Check("ollama", "FAIL", "not found; run: ai-office-kernel setup --auto --skip-gemini-auth")]

    result = run_capture(["ollama", "list"], timeout=10)
    if result.returncode != 0:
        return [
            Check("ollama", "WARN", f"{path}; server not responding. Try: ollama serve"),
        ]

    checks.append(Check("ollama", "OK", path))
    client = OllamaClient(
        base_url=settings.ollama_base_url,
        default_model=settings.router_model,
        timeout_seconds=10,
    )
    try:
        api_models = set(client.list_models())
    except OllamaError as exc:
        checks.append(Check("ollama api", "FAIL", f"{settings.ollama_base_url}: {exc}"))
        api_models = set()
    else:
        checks.append(Check("ollama api", "OK", settings.ollama_base_url))

    models = parse_ollama_model_names(result.stdout)
    models.update(api_models)
    required = {settings.router_model}
    if settings.developer_backend == "local":
        required.add(settings.local_coder_model)
    if settings.qa_backend == "local":
        required.add(settings.local_qa_model)

    for model in sorted(required):
        if model in models:
            checks.append(Check(f"ollama model {model}", "OK", "installed"))
        else:
            checks.append(Check(f"ollama model {model}", "WARN", f"missing; run: ollama pull {model}"))
    return checks


def check_telegram(settings: Settings) -> Check:
    if settings.telegram_bot_token and settings.allowed_chat_ids:
        return Check("telegram", "OK", f"{len(settings.allowed_chat_ids)} allowed chat id(s)")
    if settings.telegram_bot_token:
        return Check("telegram", "WARN", "token set, but AI_OFFICE_ALLOWED_CHAT_IDS is empty")
    return Check("telegram", "WARN", "TELEGRAM_BOT_TOKEN is not set; telegram command will not start")


def run_capture(args: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def parse_ollama_model_names(output: str) -> set[str]:
    names: set[str] = set()
    for index, line in enumerate(output.splitlines()):
        if index == 0 and line.lower().startswith("name"):
            continue
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names


def parse_json_object(text: str) -> dict[str, object] | None:
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
