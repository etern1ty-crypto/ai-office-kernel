import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_office_kernel.cli import CLIResult
from ai_office_kernel.config import Settings
from ai_office_kernel.orchestrator import AgentOrchestrator


class FakeAdapter:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return CLIResult(
            output=self.output,
            raw_output=self.output,
            exit_code=0,
            timed_out=False,
            duration_seconds=0.01,
        )


class FakeOllama:
    def __init__(self):
        self.unloaded = False

    def generate(self, prompt, **kwargs):
        return "local answer"

    def unload_model(self, model=None):
        self.unloaded = True
        return True


class OrchestratorTest(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings(
            telegram_bot_token=None,
            allowed_chat_ids=set(),
            workspace_root=root,
            prompt_dir=root / "prompts",
            memory_messages=10,
            qa_enabled=True,
            ollama_base_url="http://localhost:11434",
            router_model="llama3.1:8b-instruct-q4_K_M",
            developer_backend="gemini",
            qa_backend="gemini",
            local_coder_model="qwen2.5-coder:7b-instruct-q4_K_M",
            local_qa_model="llama3.1:8b-instruct-q4_K_M",
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

    def test_developer_path_runs_qa_and_unloads_model(self):
        with TemporaryDirectory() as tempdir:
            ollama = FakeOllama()
            dev = FakeAdapter("dev answer")
            qa = FakeAdapter("qa answer")
            orchestrator = AgentOrchestrator(
                self._settings(Path(tempdir)),
                ollama=ollama,
                developer_adapter=dev,
                qa_adapter=qa,
            )

            response = orchestrator.handle_text(1, 2, "@dev write parser")

            self.assertEqual(response.role_id, "developer")
            self.assertTrue(ollama.unloaded)
            self.assertIn("dev answer", response.text)
            self.assertIn("QA review:\nqa answer", response.text)
            self.assertEqual(len(dev.prompts), 1)
            self.assertEqual(len(qa.prompts), 1)

    def test_secretary_uses_local_model(self):
        with TemporaryDirectory() as tempdir:
            orchestrator = AgentOrchestrator(
                self._settings(Path(tempdir)),
                ollama=FakeOllama(),
                developer_adapter=FakeAdapter("dev"),
                qa_adapter=FakeAdapter("qa"),
            )

            response = orchestrator.handle_text(1, 2, "@sec hello")

            self.assertEqual(response.role_id, "secretary")
            self.assertEqual(response.text, "local answer")

    def test_local_developer_backend_uses_ollama(self):
        with TemporaryDirectory() as tempdir:
            settings = self._settings(Path(tempdir))
            settings = Settings(
                **{
                    **settings.__dict__,
                    "developer_backend": "local",
                    "qa_enabled": False,
                }
            )
            orchestrator = AgentOrchestrator(
                settings,
                ollama=FakeOllama(),
                developer_adapter=FakeAdapter("dev"),
                qa_adapter=FakeAdapter("qa"),
            )

            response = orchestrator.handle_text(1, 2, "@dev write parser")

            self.assertEqual(response.role_id, "developer")
            self.assertEqual(response.text, "local answer")


if __name__ == "__main__":
    unittest.main()
