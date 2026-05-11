import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_office_kernel.agent_loop import SecretaryAgentLoop
from ai_office_kernel.config import Settings
from ai_office_kernel.memory import SharedMemory
from ai_office_kernel.tools import ToolRuntime


class FakeOllamaChat:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)


def settings(root: Path) -> Settings:
    return Settings(
        telegram_bot_token=None,
        allowed_chat_ids=set(),
        workspace_root=root,
        prompt_dir=root / "prompts",
        memory_messages=10,
        qa_enabled=False,
        ollama_base_url="http://localhost:11434",
        router_model="qwen3:8b",
        developer_backend="gemini",
        qa_backend="gemini",
        local_coder_model="qwen2.5-coder:7b-instruct-q4_K_M",
        local_qa_model="qwen3:8b",
        gemini_command="gemini",
        gemini_model="auto",
        gemini_output_format="json",
        gemini_approval_mode="auto_edit",
        gemini_skip_trust=True,
        gemini_sandbox=False,
        gemini_all_files=False,
        gemini_include_directories=(),
        gemini_allowed_tools=(),
        gemini_resume=None,
        cli_timeout_seconds=30,
        progress_first_seconds=45,
        progress_interval_seconds=60,
        auto_confirm_cli=True,
        show_usage=True,
    )


class AgentLoopTest(unittest.TestCase):
    def test_exact_answer_does_not_expose_tools(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ollama = FakeOllamaChat([{"message": {"content": "OK"}}])
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(root),
            )

            result = loop.run(1, 2, "Ответь ровно: OK")

        self.assertEqual(result.text, "OK")
        self.assertEqual(result.events, [])
        self.assertIsNone(ollama.calls[0][1]["tools"])
        self.assertEqual(ollama.calls[0][0][-1]["content"], "Ответь ровно: OK")

    def test_exact_answer_ignores_json_actions(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ollama = FakeOllamaChat(
                [
                    {
                        "message": {
                            "content": (
                                '{"user_message":"OK","actions":[{"tool":"list_dir",'
                                '"args":{"path":"."}}]}'
                            )
                        }
                    }
                ]
            )
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(root),
            )

            result = loop.run(1, 2, "Ответь ровно: OK")

        self.assertEqual(result.events, [])
        self.assertEqual(result.text, "OK")

    def test_json_action_executes_safe_tool_then_finishes(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "README.md").write_text("hello", encoding="utf-8")
            ollama = FakeOllamaChat(
                [
                    {
                        "message": {
                            "content": (
                                '{"user_message":"Проверяю файлы",'
                                '"running_command_label":"Смотрю workspace",'
                                '"actions":[{"tool":"list_dir","args":{"path":"."},'
                                '"reason":"нужна структура"}]}'
                            )
                        }
                    },
                    {"message": {"content": "Готово, README.md есть."}},
                ]
            )
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(root),
            )

            result = loop.run(1, 2, "проверь файлы")

        self.assertEqual(result.status, "done")
        self.assertIn("README.md", ollama.calls[1][0][-1]["content"])
        self.assertIn("Готово", result.text)
        self.assertIn("status", [event.kind for event in result.events])
        self.assertIn("tool_result", [event.kind for event in result.events])

    def test_gemini_escalation_requires_confirmation(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ollama = FakeOllamaChat(
                [
                    {
                        "message": {
                            "content": (
                                '{"user_message":"Нужно передать старшему разработчику",'
                                '"actions":[{"tool":"ask_gemini_cli",'
                                '"args":{"prompt":"создай проект"},'
                                '"reason":"большая задача"}]}'
                            )
                        }
                    },
                    {"message": {"content": "Gemini ответил, проект описан."}},
                ]
            )
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(
                    root,
                    ask_gemini_cli=lambda prompt: "gemini result",
                ),
            )

            result = loop.run(1, 2, "сделай проект")
            resumed = loop.resume_confirmed(result.pending)

        self.assertEqual(result.status, "need_confirm")
        self.assertIsNotNone(result.pending)
        self.assertEqual(result.pending.request.name, "ask_gemini_cli")
        self.assertEqual(result.pending.request.risk, "danger")
        self.assertEqual(resumed.status, "done")
        self.assertIn("Gemini", resumed.text)

    def test_github_publish_does_not_immediately_escalate_to_gemini(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ollama = FakeOllamaChat(
                [
                    {
                        "message": {
                            "content": (
                                '{"user_message":"Передам в Gemini",'
                                '"actions":[{"tool":"ask_gemini_cli",'
                                '"args":{"prompt":"Опубликовать проект на GitHub, сделать .gitignore и scan secrets"},'
                                '"reason":"публикация"}]}'
                            )
                        }
                    },
                    {"message": {"content": "Сначала проверю проект локально."}},
                ]
            )
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(root),
            )

            result = loop.run(1, 2, "опубликуй проект на github")

        self.assertEqual(result.status, "done")
        self.assertIsNone(result.pending)
        self.assertIn("Сначала", result.text)
        self.assertIn("status", [event.kind for event in result.events])

    def test_short_continuation_keeps_task_context_and_tools(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            ollama = FakeOllamaChat(
                [
                    {"message": {"content": "Сначала надо проверить проект локально."}},
                    {"message": {"content": "Ок, смотрю workspace перед публикацией."}},
                ]
            )
            loop = SecretaryAgentLoop(
                settings(root),
                ollama=ollama,
                memory=SharedMemory(),
                tool_runtime_factory=lambda chat_id: ToolRuntime(root),
            )

            loop.run(1, 2, "Надо залить проект на GitHub, проверить .gitignore и токены")
            result = loop.run(1, 2, "Давай")

        second_messages, second_kwargs = ollama.calls[1]
        second_user_content = second_messages[-1]["content"]
        self.assertIsNotNone(second_kwargs["tools"])
        self.assertIn("Recent context", second_user_content)
        self.assertIn("Надо залить проект", second_user_content)
        self.assertIn("Давай", second_user_content)
        self.assertIn("workspace", result.text)


if __name__ == "__main__":
    unittest.main()
