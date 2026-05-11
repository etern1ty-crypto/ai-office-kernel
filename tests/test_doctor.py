import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ai_office_kernel.doctor import (
    check_gemini_service_settings,
    check_prompt_dir,
    parse_json_object,
    parse_ollama_model_names,
)
from ai_office_kernel.installer import write_gemini_service_settings


class DoctorTest(unittest.TestCase):
    def test_parse_ollama_model_names(self):
        output = """NAME                         ID              SIZE      MODIFIED
llama3.1:8b-instruct-q4_K_M  abc123          4.9 GB    1 hour ago
qwen2.5-coder:7b             def456          4.7 GB    1 hour ago
"""

        self.assertEqual(
            parse_ollama_model_names(output),
            {"llama3.1:8b-instruct-q4_K_M", "qwen2.5-coder:7b"},
        )

    def test_parse_json_object_with_noise(self):
        self.assertEqual(
            parse_json_object('warning\n{"response":"OK"}\n'),
            {"response": "OK"},
        )

    def test_gemini_service_settings_check(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            workspace = root / "workspace"
            workspace.mkdir()
            write_gemini_service_settings(root, quiet=True)
            write_gemini_service_settings(workspace, quiet=True)
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                checks = check_gemini_service_settings(
                    SimpleNamespace(workspace_root=workspace)
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual([check.status for check in checks], ["OK", "OK"])

    def test_prompt_dir_check(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir()
            for name in ("secretary", "developer", "qa"):
                (prompt_dir / f"{name}.md").write_text("prompt", encoding="utf-8")

            check = check_prompt_dir(SimpleNamespace(prompt_dir=prompt_dir))

        self.assertEqual(check.status, "OK")


if __name__ == "__main__":
    unittest.main()
