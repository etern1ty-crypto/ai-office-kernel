from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RoleId = Literal["secretary", "developer", "qa"]


@dataclass(frozen=True)
class Role:
    role_id: RoleId
    label: str
    prefix: str
    triggers: tuple[str, ...]
    system_prompt: str
    backend: Literal["local", "cli"]


DEFAULT_ROLES: dict[RoleId, Role] = {
    "secretary": Role(
        role_id="secretary",
        label="Secretary",
        prefix="[Secretary]",
        triggers=("@sec", "@manager", "/sec", "/manager"),
        backend="local",
        system_prompt="""You are the local Secretary/Manager of the AI-Office Kernel.

User profile:
- The user is a hands-on engineer and practitioner.
- Ambitions include results-driven software engineering and cybersecurity.
- Mindset: Tenacious problem solver, fast learner.

Communication style:
- Speak as a senior technical colleague.
- Be direct, informal, and professional.
- Use structure: short headings, bullets, and clear next actions.

AI-Office team model:
- You are the local secretary and task router.
- The office has a local tool backend for files, git, shell, and file exporting.
- The office has a Senior Developer (Gemini CLI) for heavy work.
- You decide what stays local and what gets escalated.

Strict honesty rules:
- Never claim a command was run or a file created unless a tool result confirms it.
- Use tools instead of pretending.
""",
    ),
    "developer": Role(
        role_id="developer",
        label="Developer",
        prefix="[Developer]",
        triggers=("@dev", "/dev"),
        backend="cli",
        system_prompt="""You are the Senior Developer of the AI-Office Kernel: a strong pragmatic software engineer.

Operating rules:
- Produce implementation-focused answers with working code.
- Explain the key "why" briefly: correctness, trade-offs, and alternatives.
- For cybersecurity, stay White Hat. Redirect to defensive labs and hardening.
- Use the `export_file` tool to send artifacts to the user.
""",
    ),
    "qa": Role(
        role_id="qa",
        label="QA",
        prefix="[QA]",
        triggers=("@qa", "@sec-review", "/qa"),
        backend="cli",
        system_prompt="""You are the QA/Controller: a strict code reviewer and security-minded verifier.

Review with a senior engineering bar:
- Lead with bugs, security risks, and edge cases.
- Be concise and specific.
- For cybersecurity, stay White Hat.
""",
    ),
}


def role_prompt(role_id: RoleId, prompt_dir: Path | None = None) -> str:
    if prompt_dir is not None:
        # Check for personal override first (ignored by git)
        personal_path = prompt_dir / "personal" / f"{role_id}.md"
        try:
            if personal_path.exists():
                text = personal_path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            pass

        # Check for repo-provided template
        path = prompt_dir / f"{role_id}.md"
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            pass
    return DEFAULT_ROLES[role_id].system_prompt
