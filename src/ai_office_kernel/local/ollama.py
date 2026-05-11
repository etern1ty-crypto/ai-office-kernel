from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class OllamaError(RuntimeError):
    pass


@dataclass
class OllamaClient:
    base_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen3:8b"
    timeout_seconds: int = 60

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        keep_alive: str | int = "5m",
    ) -> str:
        payload: dict[str, object] = {
            "model": model or self.default_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
        }
        if system:
            payload["system"] = system
        response = self._post_json("/api/generate", payload)
        if "error" in response:
            raise OllamaError(str(response["error"]))
        return str(response.get("response", "")).strip()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        think: bool | None = None,
        keep_alive: str | int = "5m",
    ) -> dict[str, Any]:
        payload: dict[str, object] = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": stream,
            "keep_alive": keep_alive,
        }
        if tools is not None:
            payload["tools"] = tools
        if think is not None:
            payload["think"] = think
        response = self._post_json("/api/chat", payload)
        if "error" in response:
            raise OllamaError(str(response["error"]))
        return response

    def unload_model(self, model: str | None = None) -> bool:
        payload = {
            "model": model or self.default_model,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }
        self._post_json("/api/generate", payload)
        return True

    def list_models(self) -> list[str]:
        response = self._get_json("/api/tags")
        models = response.get("models", [])
        if not isinstance(models, list):
            return []
        names: list[str] = []
        for model in models:
            if isinstance(model, dict) and isinstance(model.get("name"), str):
                names.append(model["name"])
        return names

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        url = self.base_url.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OllamaError(str(exc)) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"invalid Ollama response: {body[:200]}") from exc

    def _get_json(self, path: str) -> dict[str, object]:
        url = self.base_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise OllamaError(str(exc)) from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"invalid Ollama response: {body[:200]}") from exc
