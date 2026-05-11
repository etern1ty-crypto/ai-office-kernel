from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceTools:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    def resolve(self, user_path: str | Path) -> Path:
        path = Path(user_path)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"path escapes workspace: {user_path}")
        return resolved

    def read_text(self, user_path: str | Path, max_bytes: int = 200_000) -> str:
        path = self.resolve(user_path)
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError(f"file is too large for context: {path}")
        return data.decode("utf-8")

    def write_text(
        self,
        user_path: str | Path,
        content: str,
        *,
        overwrite: bool = False,
    ) -> Path:
        path = self.resolve(user_path)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

