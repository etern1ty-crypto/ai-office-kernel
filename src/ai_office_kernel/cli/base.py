from __future__ import annotations

import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SPINNER_RE = re.compile(
    r"^\s*(?:[|/\\-]\s*)?(?:loading|thinking|generating|working|please wait|"
    r"обработка|загрузка|генерация)?\.{0,3}\s*$",
    re.IGNORECASE,
)
NOISE_LINE_RE = re.compile(
    r"^(?:Warning: True color .*|Ripgrep is not available\. Falling back to GrepTool\.)$"
)


@dataclass(frozen=True)
class CLIResult:
    output: str
    raw_output: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    session_id: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def usage_summary(self) -> str:
        stats = self.stats or {}
        if "total_tokens" in stats:
            parts = [
                f"tokens={stats.get('total_tokens', 0)}",
                f"in={stats.get('input_tokens', 0)}",
                f"out={stats.get('output_tokens', 0)}",
            ]
            if stats.get("cached"):
                parts.append(f"cached={stats.get('cached', 0)}")
            if stats.get("tool_calls"):
                parts.append(f"tools={stats.get('tool_calls', 0)}")
            return " ".join(parts)

        models = stats.get("models")
        if isinstance(models, dict):
            totals = _sum_model_tokens(models)
            if totals:
                parts = [
                    f"tokens={totals['total']}",
                    f"in={totals['input']}",
                    f"out={totals['output']}",
                ]
                tools = stats.get("tools")
                if isinstance(tools, dict) and tools.get("totalCalls"):
                    parts.append(f"tools={tools['totalCalls']}")
                return " ".join(parts)
        return ""


def _sum_model_tokens(models: dict[str, Any]) -> dict[str, int]:
    totals = {"total": 0, "input": 0, "output": 0}
    found = False
    for model_stats in models.values():
        if not isinstance(model_stats, dict):
            continue
        tokens = model_stats.get("tokens")
        if not isinstance(tokens, dict):
            continue
        found = True
        totals["total"] += int(tokens.get("total") or tokens.get("total_tokens") or 0)
        totals["input"] += int(tokens.get("prompt") or tokens.get("input_tokens") or 0)
        totals["output"] += int(
            tokens.get("candidates") or tokens.get("output_tokens") or 0
        )
    return totals if found else {}


def clean_terminal_output(text: str) -> str:
    without_ansi = ANSI_RE.sub("", text)
    without_controls = without_ansi.replace("\r", "\n").replace("\b", "")

    cleaned_lines: list[str] = []
    for raw_line in without_controls.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            cleaned_lines.append("")
            continue
        if SPINNER_RE.match(line):
            continue
        if NOISE_LINE_RE.match(line.strip()):
            continue
        if re.match(r"^\s*[|/\\]\s+\S", line):
            line = re.sub(r"^\s*[|/\\]\s+", "", line)
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


class BaseCLIAdapter(ABC):
    def __init__(
        self,
        command: str,
        timeout_seconds: int = 180,
        auto_confirm: bool = True,
    ):
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.auto_confirm = auto_confirm

    @abstractmethod
    def build_command(self, prompt: str) -> Sequence[str]:
        raise NotImplementedError

    def ask(self, prompt: str) -> CLIResult:
        started = time.monotonic()
        command = list(self.build_command(prompt))
        input_text = ("y\n" * 8) if self.auto_confirm else None
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            raw_output = "\n".join(
                item for item in (completed.stdout, completed.stderr) if item
            )
            return CLIResult(
                output=clean_terminal_output(raw_output),
                raw_output=raw_output,
                exit_code=completed.returncode,
                timed_out=False,
                duration_seconds=time.monotonic() - started,
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
            raw_output = "\n".join(item for item in (stdout, stderr) if item)
            return CLIResult(
                output=clean_terminal_output(raw_output),
                raw_output=raw_output,
                exit_code=None,
                timed_out=True,
                duration_seconds=time.monotonic() - started,
                stdout=stdout,
                stderr=stderr,
            )

    def _split_command(self) -> list[str]:
        return shlex.split(self.command)
