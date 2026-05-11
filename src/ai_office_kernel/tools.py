from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal


class ToolError(RuntimeError):
    pass


RiskLevel = Literal["safe", "medium", "danger"]
ToolCallback = Callable[[str], str]


@dataclass(frozen=True)
class ToolExecution:
    name: str
    args: dict[str, Any]
    risk: RiskLevel
    ok: bool
    content: str

    def as_tool_message(self) -> str:
        status = "ok" if self.ok else "error"
        return f"[TOOL {self.name} {status} risk={self.risk}]\n{self.content}"


@dataclass(frozen=True)
class ToolRequest:
    name: str
    args: dict[str, Any]
    risk: RiskLevel
    reason: str = ""


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: Path
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    def text(self, limit: int = 3500) -> str:
        parts = [
            "[COMMAND]",
            f"$ {self.command}",
            f"cwd: {self.cwd}",
            f"exit: {'timeout' if self.timed_out else self.exit_code}",
        ]
        output = "\n".join(item for item in (self.stdout, self.stderr) if item).strip()
        if output:
            if len(output) > limit:
                output = output[:limit] + "\n... output truncated ..."
            parts.extend(["", output])
        return "\n".join(parts)


@dataclass
class BackgroundProcess:
    process_id: int
    command: str
    cwd: Path
    process: subprocess.Popen[str]

    def status_text(self) -> str:
        code = self.process.poll()
        status = "running" if code is None else f"exited {code}"
        return f"[PROCESS {self.process_id}] {status}\n$ {self.command}\ncwd: {self.cwd}"


@dataclass(frozen=True)
class PendingCommand:
    command: str
    cwd: Path
    background: bool
    timeout_seconds: int

    def confirm_text(self) -> str:
        mode = "background" if self.background else "foreground"
        return (
            "[CONFIRM COMMAND]\n"
            f"mode: {mode}\n"
            f"cwd: {self.cwd}\n"
            f"$ {self.command}\n\n"
            "Подтвердить: /confirm_tool\n"
            "Отмена: /cancel_tool"
        )


class WorkspaceToolRunner:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self._next_process_id = 1
        self.processes: dict[int, BackgroundProcess] = {}

    def resolve_path(self, raw_path: str | None = None) -> Path:
        raw_path = (raw_path or ".").strip() or "."
        if raw_path == "/workspace":
            raw_path = "."
        elif raw_path.startswith("/workspace/"):
            raw_path = raw_path.removeprefix("/workspace/")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        resolved = candidate.resolve()
        if resolved != self.workspace_root and self.workspace_root not in resolved.parents:
            raise ToolError(f"path is outside workspace: {raw_path}")
        return resolved

    def list_dir(self, raw_path: str | None = None, *, limit: int = 80) -> str:
        path = self.resolve_path(raw_path)
        if not path.exists():
            raise ToolError(f"path does not exist: {path}")
        if not path.is_dir():
            raise ToolError(f"path is not a directory: {path}")
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        lines = [f"[FILES] {path}"]
        for entry in entries[:limit]:
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"- {entry.name}{suffix}")
        if len(entries) > limit:
            lines.append(f"... {len(entries) - limit} more")
        return "\n".join(lines)

    def read_file(self, raw_path: str, *, limit: int = 3500) -> str:
        path = self.resolve_path(raw_path)
        if not path.exists():
            raise ToolError(f"path does not exist: {path}")
        if not path.is_file():
            raise ToolError(f"path is not a file: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > limit:
            text = text[:limit] + "\n... file truncated ..."
        return f"[FILE] {path}\n\n{text}"

    def prepare_command(
        self,
        text: str,
        *,
        background: bool = False,
        default_timeout_seconds: int = 120,
    ) -> PendingCommand:
        cwd, command, timeout_seconds = parse_command_options(
            text,
            workspace_root=self.workspace_root,
            default_timeout_seconds=default_timeout_seconds,
        )
        validate_command(command)
        return PendingCommand(
            command=command,
            cwd=cwd,
            background=background,
            timeout_seconds=timeout_seconds,
        )

    def run_command(self, pending: PendingCommand) -> CommandResult | BackgroundProcess:
        args = shlex.split(pending.command)
        if pending.background:
            process = subprocess.Popen(
                args,
                cwd=pending.cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            process_id = self._next_process_id
            self._next_process_id += 1
            background = BackgroundProcess(
                process_id=process_id,
                command=pending.command,
                cwd=pending.cwd,
                process=process,
            )
            self.processes[process_id] = background
            return background

        try:
            completed = subprocess.run(
                args,
                cwd=pending.cwd,
                capture_output=True,
                text=True,
                timeout=pending.timeout_seconds,
                check=False,
            )
            return CommandResult(
                command=pending.command,
                cwd=pending.cwd,
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            return CommandResult(
                command=pending.command,
                cwd=pending.cwd,
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )

    def process_status(self) -> str:
        if not self.processes:
            return "Фоновых процессов нет."
        return "\n\n".join(process.status_text() for process in self.processes.values())

    def stop_process(self, process_id: int) -> str:
        process = self.processes.get(process_id)
        if process is None:
            raise ToolError(f"unknown process id: {process_id}")
        if process.process.poll() is None:
            process.process.terminate()
        return process.status_text()


class ToolRuntime:
    def __init__(
        self,
        workspace_root: Path,
        *,
        workspace: WorkspaceToolRunner | None = None,
        ask_local_coder: ToolCallback | None = None,
        ask_gemini_cli: ToolCallback | None = None,
        request_gemini_model_change: ToolCallback | None = None,
    ):
        self.workspace = workspace or WorkspaceToolRunner(workspace_root)
        self.ask_local_coder = ask_local_coder
        self.ask_gemini_cli = ask_gemini_cli
        self.request_gemini_model_change = request_gemini_model_change

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            _tool_schema("workspace_info", "Show the configured workspace root and top-level files.", {}),
            _tool_schema("list_dir", "List files inside the workspace.", {"path": "string"}),
            _tool_schema("read_file", "Read a UTF-8 file inside the workspace.", {"path": "string"}),
            _tool_schema("make_dir", "Create a directory inside the workspace.", {"path": "string"}),
            _tool_schema(
                "write_file",
                "Create or replace a UTF-8 text file inside the workspace. Use this for simple text files instead of shell commands.",
                {"path": "string", "content": "string"},
            ),
            _tool_schema(
                "grep_project",
                "Search text inside project files.",
                {"pattern": "string", "path": "string"},
            ),
            _tool_schema("git_status", "Show git status for a workspace path.", {"path": "string"}),
            _tool_schema("git_diff", "Show git diff for a workspace path.", {"path": "string"}),
            _tool_schema(
                "scan_secrets",
                "Scan project files for likely leaked tokens, private keys, and API credentials before publishing.",
                {"path": "string"},
            ),
            _tool_schema(
                "run_shell_safe",
                "Run one non-destructive command inside the workspace.",
                {"command": "string", "cwd": "string", "timeout_seconds": "number"},
            ),
            _tool_schema(
                "run_background",
                "Start one long-running command inside the workspace.",
                {"command": "string", "cwd": "string"},
            ),
            _tool_schema("process_status", "Show background process status.", {}),
            _tool_schema("stop_process", "Stop a background process by id.", {"process_id": "number"}),
            _tool_schema(
                "apply_patch",
                "Replace text in one file inside the workspace.",
                {"path": "string", "old_string": "string", "new_string": "string"},
            ),
            _tool_schema(
                "export_file",
                "Export a file from the workspace to the user (e.g. for downloading).",
                {"path": "string"},
            ),
            _tool_schema("fetch_url", "Fetch a web page URL.", {"url": "string"}),
            _tool_schema("web_search", "Search the web for current information.", {"query": "string"}),
            _tool_schema("ask_local_coder", "Ask the local coder model for a small coding task.", {"prompt": "string"}),
            _tool_schema("ask_gemini_cli", "Escalate a hard coding task to Gemini CLI.", {"prompt": "string"}),
            _tool_schema(
                "request_gemini_model_change",
                "Request a Gemini CLI model change; user confirmation is required before it is applied.",
                {"model": "string"},
            ),
        ]

    def request_from_action(self, action: dict[str, Any]) -> ToolRequest:
        name = str(action.get("tool") or action.get("name") or "").strip()
        args = action.get("args")
        if not isinstance(args, dict):
            args = {}
        risk = classify_tool(name, args)
        if name == "write_file":
            try:
                path = self.workspace.resolve_path(str(args.get("path") or ""))
            except ToolError:
                pass
            else:
                if path.exists():
                    risk = "danger"
        reason = str(action.get("reason") or "")
        return ToolRequest(name=name, args=args, risk=risk, reason=reason)

    def execute(self, request: ToolRequest) -> ToolExecution:
        try:
            content = self._execute(request.name, request.args)
            return ToolExecution(
                name=request.name,
                args=request.args,
                risk=request.risk,
                ok=True,
                content=content,
            )
        except Exception as exc:  # noqa: BLE001 - tool errors must be returned to the model.
            return ToolExecution(
                name=request.name,
                args=request.args,
                risk=request.risk,
                ok=False,
                content=str(exc),
            )

    def _execute(self, name: str, args: dict[str, Any]) -> str:
        if name == "workspace_info":
            return self._workspace_info()
        if name == "list_dir":
            return self.workspace.list_dir(str(args.get("path") or "."))
        if name == "read_file":
            return self.workspace.read_file(str(args.get("path") or ""))
        if name == "make_dir":
            return self._make_dir(str(args.get("path") or ""))
        if name == "write_file":
            return self._write_file(
                str(args.get("path") or ""),
                str(args.get("content") or ""),
            )
        if name == "grep_project":
            return self._grep_project(
                str(args.get("pattern") or ""),
                str(args.get("path") or "."),
            )
        if name == "git_status":
            return self._git(["status", "--short"], str(args.get("path") or "."))
        if name == "git_diff":
            return self._git(["diff", "--", "."], str(args.get("path") or "."))
        if name == "scan_secrets":
            return self._scan_secrets(str(args.get("path") or "."))
        if name == "run_shell_safe":
            command = str(args.get("command") or "")
            cwd = str(args.get("cwd") or ".")
            timeout = int(args.get("timeout_seconds") or 120)
            pending = self.workspace.prepare_command(
                f"--cwd {shlex.quote(cwd)} --timeout {timeout} {command}",
                background=False,
                default_timeout_seconds=timeout,
            )
            result = self.workspace.run_command(pending)
            if isinstance(result, BackgroundProcess):
                return result.status_text()
            return result.text()
        if name == "run_background":
            command = str(args.get("command") or "")
            cwd = str(args.get("cwd") or ".")
            pending = self.workspace.prepare_command(
                f"--cwd {shlex.quote(cwd)} {command}",
                background=True,
            )
            result = self.workspace.run_command(pending)
            if not isinstance(result, BackgroundProcess):
                return result.text()
            return result.status_text()
        if name == "process_status":
            return self.workspace.process_status()
        if name == "stop_process":
            return self.workspace.stop_process(int(args.get("process_id") or 0))
        if name == "apply_patch":
            return self._replace_text(
                str(args.get("path") or ""),
                str(args.get("old_string") or ""),
                str(args.get("new_string") or ""),
            )
        if name == "export_file":
            return self._export_file(str(args.get("path") or ""))
        if name == "fetch_url":
            return fetch_url(str(args.get("url") or ""))
        if name == "web_search":
            return web_search(str(args.get("query") or ""))
        if name == "ask_local_coder":
            if self.ask_local_coder is None:
                raise ToolError("local coder callback is not configured")
            return self.ask_local_coder(str(args.get("prompt") or ""))
        if name == "ask_gemini_cli":
            if self.ask_gemini_cli is None:
                raise ToolError("Gemini CLI callback is not configured")
            return self.ask_gemini_cli(str(args.get("prompt") or ""))
        if name == "request_gemini_model_change":
            if self.request_gemini_model_change is None:
                raise ToolError("Gemini model change callback is not configured")
            return self.request_gemini_model_change(str(args.get("model") or ""))
        raise ToolError(f"unknown tool: {name}")

    def _grep_project(self, pattern: str, raw_path: str) -> str:
        if not pattern:
            raise ToolError("grep pattern is required")
        root = self.workspace.resolve_path(raw_path)
        rg = shutil.which("rg")
        if rg:
            completed = subprocess.run(
                [rg, "--line-number", "--max-count", "50", pattern, str(root)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            output = completed.stdout or completed.stderr
            return output[:3500] or "No matches."

        matches: list[str] = []
        regex = re.compile(pattern)
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            try:
                for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                    if regex.search(line):
                        matches.append(f"{path}:{number}:{line}")
                        if len(matches) >= 50:
                            return "\n".join(matches)
            except OSError:
                continue
        return "\n".join(matches) if matches else "No matches."

    def _git(self, args: list[str], raw_path: str) -> str:
        cwd = self.workspace.resolve_path(raw_path)
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        output = "\n".join(item for item in (completed.stdout, completed.stderr) if item).strip()
        return output[:3500] or f"git {' '.join(args)} returned no output"

    def _replace_text(self, raw_path: str, old_string: str, new_string: str) -> str:
        if not old_string:
            raise ToolError("old_string is required")
        path = self.workspace.resolve_path(raw_path)
        if not path.is_file():
            raise ToolError(f"path is not a file: {path}")
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            raise ToolError("old_string was not found")
        path.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Updated {path}"

    def _make_dir(self, raw_path: str) -> str:
        path = self.workspace.resolve_path(raw_path)
        path.mkdir(parents=True, exist_ok=True)
        return f"Created directory {path}"

    def _write_file(self, raw_path: str, content: str) -> str:
        if not raw_path.strip():
            raise ToolError("path is required")
        path = self.workspace.resolve_path(raw_path)
        parent = path.parent
        if parent != self.workspace.workspace_root and self.workspace.workspace_root not in parent.parents:
            raise ToolError(f"parent path is outside workspace: {parent}")
        parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {path} ({len(content)} chars)"

    def _export_file(self, raw_path: str) -> str:
        path = self.workspace.resolve_path(raw_path)
        if not path.exists():
            raise ToolError(f"path does not exist: {path}")
        if not path.is_file():
            raise ToolError(f"path is not a file: {path}")
        return f"[EXPORT] {path}"

    def _workspace_info(self) -> str:
        return f"[WORKSPACE]\nRoot: {self.workspace.workspace_root}\nAlias: /workspace\n\n{self.workspace.list_dir('.')}"

    def _scan_secrets(self, raw_path: str) -> str:
        root = self.workspace.resolve_path(raw_path)
        paths = [root] if root.is_file() else root.rglob("*")
        findings: list[str] = []
        for path in paths:
            if not path.is_file() or _should_skip_secret_scan(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if _looks_like_secret_line(line):
                    rel = path.relative_to(self.workspace.workspace_root)
                    findings.append(f"{rel}:{line_no}: {_redact_secret_line(line)}")
                    if len(findings) >= 80:
                        return "[SECRET SCAN]\nPotential secrets found:\n" + "\n".join(findings)
        if not findings:
            return "[SECRET SCAN]\nNo likely secrets found."
        return "[SECRET SCAN]\nPotential secrets found:\n" + "\n".join(findings)


def classify_tool(name: str, args: dict[str, Any]) -> RiskLevel:
    if name in {
        "workspace_info",
        "list_dir",
        "read_file",
        "grep_project",
        "git_status",
        "git_diff",
        "scan_secrets",
        "fetch_url",
        "web_search",
        "process_status",
        "ask_local_coder",
        "export_file",
    }:
        return "safe"
    if name in {"make_dir", "write_file"}:
        return "medium"
    if name == "request_gemini_model_change":
        return "medium"
    if name == "ask_gemini_cli":
        return "danger"
    if name in {"run_shell_safe", "run_background", "apply_patch", "stop_process"}:
        command = str(args.get("command") or "")
        if name in {"run_shell_safe", "run_background"} and _looks_dangerous_command(command):
            return "danger"
        return "medium"
    return "danger"


def auto_execute_risk(risk: RiskLevel) -> bool:
    return risk in {"safe", "medium"}


def _looks_dangerous_command(command: str) -> bool:
    if _has_blocked_shell_syntax(command):
        return False
    try:
        validate_command(command)
    except ToolError:
        return True
    parts = shlex.split(command)
    executable = Path(parts[0]).name if parts else ""
    if executable == "git":
        dangerous_git = {"push", "commit", "reset", "remote", "tag", "checkout", "switch"}
        return len(parts) > 1 and parts[1] in dangerous_git
    if executable == "gh":
        return _is_dangerous_gh_command(parts)
    return False


def _has_blocked_shell_syntax(command: str) -> bool:
    return any(token in command for token in ("&&", "||", ";", "|", ">", "<", "$(", "`"))


def _is_dangerous_gh_command(parts: list[str]) -> bool:
    if len(parts) < 3:
        return False
    if parts[1:3] in (["repo", "create"], ["repo", "delete"], ["repo", "edit"]):
        return True
    if parts[1:3] in (["release", "create"], ["pr", "create"]):
        return True
    return parts[1:3] in (["auth", "login"], ["auth", "refresh"])


def _should_skip_secret_scan(path: Path) -> bool:
    ignored_parts = {
        ".git",
        ".venv",
        ".ai-office",
        "*.egg-info",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        "models",
    }
    if any(part in ignored_parts for part in path.parts):
        return True
    if any(part.endswith(".egg-info") for part in path.parts):
        return True
    return path.stat().st_size > 1_000_000


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
)
ENV_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?"
    r"(?P<key>[A-Z][A-Z0-9_]*)"
    r"\s*=\s*['\"]?(?P<value>[^'\"\s#]+)"
)
PLACEHOLDER_SECRET_VALUES = {
    "",
    "auto",
    "none",
    "null",
    "false",
    "true",
    "changeme",
    "change_me",
    "example",
    "placeholder",
    "token",
    "secret",
    "password",
    "your_token",
    "your_secret",
    "your_password",
}


def _looks_like_secret_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    lowered = stripped.lower()
    if "example" in lowered or "your_" in lowered:
        return False
    if any(pattern.search(stripped) for pattern in SECRET_PATTERNS):
        return True
    assignment = ENV_ASSIGNMENT.search(stripped)
    if assignment is None:
        return False
    if not _looks_like_sensitive_env_key(assignment.group("key")):
        return False
    return _looks_like_secret_value(assignment.group("value"))


def _looks_like_sensitive_env_key(key: str) -> bool:
    parts = set(key.split("_"))
    if parts & {"TOKEN", "SECRET", "PASSWORD", "PASS"}:
        return True
    joined_markers = ("API_KEY", "PRIVATE_KEY", "BOT_TOKEN", "WEBHOOK_SECRET")
    return any(marker in key for marker in joined_markers)


def _looks_like_secret_value(value: str) -> bool:
    normalized = value.strip().strip("'\"")
    lowered = normalized.lower()
    if lowered in PLACEHOLDER_SECRET_VALUES:
        return False
    if lowered.startswith(("your-", "your_", "<", "${")):
        return False
    return len(normalized) >= 12


def _redact_secret_line(line: str) -> str:
    if "=" in line:
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        return f"{key.strip()}={value[:4]}...REDACTED"
    return line[:40] + "...REDACTED"


def fetch_url(url: str, *, limit: int = 3500) -> str:
    if not url.startswith(("http://", "https://")):
        raise ToolError("only http/https URLs are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "ai-office-kernel/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read(limit + 1).decode("utf-8", errors="replace")
    if len(body) > limit:
        body = body[:limit] + "\n... page truncated ..."
    return f"[URL] {url}\n\n{body}"


def web_search(query: str, *, limit: int = 2500) -> str:
    if not query.strip():
        raise ToolError("query is required")
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = fetch_url(url, limit=limit)
    snippets = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html, flags=re.S)
    cleaned = [re.sub(r"<[^>]+>", "", item).strip() for item in snippets[:5]]
    cleaned = [item for item in cleaned if item]
    if not cleaned:
        return html
    return "[WEB SEARCH]\n" + "\n".join(f"- {item}" for item in cleaned)


def _tool_schema(name: str, description: str, properties: dict[str, str]) -> dict[str, Any]:
    schema_properties: dict[str, dict[str, str]] = {}
    required: list[str] = []
    for prop_name, prop_type in properties.items():
        schema_properties[prop_name] = {"type": prop_type}
        if prop_name not in {"path", "cwd", "timeout_seconds"}:
            required.append(prop_name)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": schema_properties,
                "required": required,
            },
        },
    }


def parse_command_options(
    text: str,
    *,
    workspace_root: Path,
    default_timeout_seconds: int,
) -> tuple[Path, str, int]:
    args = shlex.split(text)
    if not args:
        raise ToolError("empty command")
    cwd = workspace_root
    timeout_seconds = default_timeout_seconds
    remaining: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--cwd":
            index += 1
            if index >= len(args):
                raise ToolError("--cwd requires a path")
            cwd_candidate = Path(args[index]).expanduser()
            if not cwd_candidate.is_absolute():
                cwd_candidate = workspace_root / cwd_candidate
            cwd = cwd_candidate.resolve()
            if cwd != workspace_root and workspace_root not in cwd.parents:
                raise ToolError(f"cwd is outside workspace: {args[index]}")
        elif arg == "--timeout":
            index += 1
            if index >= len(args):
                raise ToolError("--timeout requires seconds")
            timeout_seconds = int(args[index])
        else:
            remaining = args[index:]
            break
        index += 1

    if not remaining:
        raise ToolError("empty command")
    command = shlex.join(remaining)
    return cwd, command, timeout_seconds


def validate_command(command: str) -> None:
    args = shlex.split(command)
    if not args:
        raise ToolError("empty command")
    executable = Path(args[0]).name
    blocked = {
        "rm",
        "rmdir",
        "sudo",
        "su",
        "dd",
        "mkfs",
        "mount",
        "umount",
        "chmod",
        "chown",
        "reboot",
        "shutdown",
        "poweroff",
    }
    if executable in blocked:
        raise ToolError(f"blocked command: {executable}")
    if any(token in command for token in ("&&", "||", ";", "|", ">", "<", "$(", "`")):
        raise ToolError("shell operators are blocked; run one command at a time")
