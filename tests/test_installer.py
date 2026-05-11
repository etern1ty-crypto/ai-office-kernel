import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_office_kernel.installer import (
    InstallerConfig,
    SetupOptions,
    run_setup,
    safe_model_name,
    write_env,
    write_default_prompts,
    write_gemini_service_settings,
)


class InstallerTest(unittest.TestCase):
    def test_safe_model_name_from_hf_url(self):
        self.assertEqual(
            safe_model_name("https://huggingface.co/user/model/resolve/main/Qwen.gguf"),
            "qwen",
        )

    def test_write_env_contains_local_backends(self):
        with TemporaryDirectory() as tempdir:
            path = Path(tempdir) / ".env"
            write_env(
                path,
                InstallerConfig(
                    telegram_bot_token="token",
                    telegram_chat_id="123",
                    workspace_root=Path("/workspace"),
                    router_model="llama",
                    developer_backend="local",
                    qa_backend="gemini",
                    local_coder_model="coder",
                    local_qa_model="qa",
                    gemini_model="auto",
                    gemini_approval_mode="auto_edit",
                    gemini_output_format="json",
                    gemini_skip_trust=True,
                    gemini_sandbox=False,
                    qa_enabled=True,
                ),
                quiet=True,
            )

            content = path.read_text(encoding="utf-8")

        self.assertIn("AI_OFFICE_DEVELOPER_BACKEND=local", content)
        self.assertIn("AI_OFFICE_LOCAL_CODER_MODEL=coder", content)
        self.assertIn("AI_OFFICE_PROMPT_DIR=", content)
        self.assertIn("AI_OFFICE_GEMINI_APPROVAL_MODE=auto_edit", content)
        self.assertIn("AI_OFFICE_CLI_TIMEOUT_SECONDS=1200", content)
        self.assertIn("AI_OFFICE_AUTO_CONFIRM_CLI=false", content)

    def test_write_default_prompts(self):
        with TemporaryDirectory() as tempdir:
            prompt_dir = write_default_prompts(Path(tempdir), quiet=True)

            self.assertTrue((prompt_dir / "secretary.md").exists())
            self.assertTrue((prompt_dir / "developer.md").exists())
            self.assertTrue((prompt_dir / "qa.md").exists())

    def test_write_gemini_service_settings_disables_subagents(self):
        with TemporaryDirectory() as tempdir:
            path = write_gemini_service_settings(Path(tempdir), quiet=True)

            content = path.read_text(encoding="utf-8")

        self.assertIn('"enableAgents": false', content)
        self.assertIn('"run_shell_command"', content)

    def test_auto_skip_mode_does_not_prompt(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

            def fail_input(*args, **kwargs):
                raise AssertionError("setup prompted unexpectedly")

            with (
                patch("builtins.input", side_effect=fail_input),
                patch("ai_office_kernel.installer.install_nodejs_if_missing", return_value=True),
                patch("ai_office_kernel.installer.shutil.which", return_value=None),
            ):
                with redirect_stdout(StringIO()):
                    run_setup(
                        root,
                        SetupOptions(
                            auto=True,
                            install_python=False,
                            install_gemini=False,
                            install_ollama=False,
                            pull_models=False,
                            run_gemini_auth=False,
                            workspace_root=root,
                        ),
                    )

            self.assertTrue((root / ".env").exists())
            self.assertTrue((root / ".gemini" / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
