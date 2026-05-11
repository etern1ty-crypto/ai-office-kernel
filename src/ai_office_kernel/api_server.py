from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ai_office_kernel.agent_loop import AgentPendingConfirmation, AgentRunResult, SecretaryAgentLoop


class AgentAPIState:
    def __init__(self, agent_loop: SecretaryAgentLoop):
        self.agent_loop = agent_loop
        self.pending: dict[int, AgentPendingConfirmation] = {}


def run_api_server(agent_loop: SecretaryAgentLoop, *, host: str = "127.0.0.1", port: int = 8787) -> None:
    state = AgentAPIState(agent_loop)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AIOfficeKernelHTTP/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API.
            if self.path.startswith("/status"):
                chat_id = _chat_id_from_query(self.path)
                pending = state.pending.get(chat_id)
                self._send_json(
                    {
                        "ok": True,
                        "chat_id": chat_id,
                        "pending_confirmation": _pending_payload(pending) if pending else None,
                    }
                )
                return
            self._send_json({"ok": False, "error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API.
            try:
                payload = self._read_json()
                chat_id = int(payload.get("chat_id") or 0)
                user_id = payload.get("user_id")
                user_id = int(user_id) if user_id is not None else None
                if self.path == "/chat":
                    text = str(payload.get("text") or "")
                    result = state.agent_loop.run(chat_id, user_id, text)
                    if result.pending is not None:
                        state.pending[chat_id] = result.pending
                    self._send_json(_result_payload(result))
                    return
                if self.path == "/confirm":
                    pending = state.pending.pop(chat_id, None)
                    if pending is None:
                        self._send_json({"ok": False, "error": "no pending confirmation"}, status=409)
                        return
                    result = state.agent_loop.resume_confirmed(pending)
                    if result.pending is not None:
                        state.pending[chat_id] = result.pending
                    self._send_json(_result_payload(result))
                    return
                if self.path == "/cancel":
                    cancelled = state.pending.pop(chat_id, None) is not None
                    self._send_json({"ok": True, "cancelled": cancelled})
                    return
                self._send_json({"ok": False, "error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001 - API should return JSON errors.
                self._send_json({"ok": False, "error": str(exc)}, status=500)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI-Office Kernel API listening on http://{host}:{port}")
    server.serve_forever()


def _result_payload(result: AgentRunResult) -> dict[str, Any]:
    return {
        "ok": result.status != "error",
        "status": result.status,
        "text": result.text,
        "events": [
            {
                "kind": event.kind,
                "label": event.label,
                "content": event.content,
                "tool": event.tool,
                "risk": event.risk,
            }
            for event in result.events
        ],
        "pending_confirmation": _pending_payload(result.pending) if result.pending else None,
    }


def _pending_payload(pending: AgentPendingConfirmation | None) -> dict[str, Any] | None:
    if pending is None:
        return None
    return {
        "tool": pending.request.name,
        "risk": pending.request.risk,
        "reason": pending.request.reason,
        "args": pending.request.args,
    }


def _chat_id_from_query(path: str) -> int:
    if "?" not in path:
        return 0
    query = path.split("?", maxsplit=1)[1]
    for item in query.split("&"):
        key, _, value = item.partition("=")
        if key == "chat_id":
            return int(value or 0)
    return 0
