import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_office_kernel.tools import (
    ToolError,
    ToolRuntime,
    WorkspaceToolRunner,
    classify_tool,
    validate_command,
)


class ToolsTest(unittest.TestCase):
    def test_list_and_read_are_workspace_scoped(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "app.txt").write_text("hello", encoding="utf-8")
            runner = WorkspaceToolRunner(root)

            self.assertIn("app.txt", runner.list_dir("."))
            self.assertIn("hello", runner.read_file("app.txt"))

            with self.assertRaises(ToolError):
                runner.read_file("/etc/passwd")

    def test_export_file_tool(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "secret.txt").write_text("my secret", encoding="utf-8")
            runtime = ToolRuntime(root)

            # Test successful export
            result = runtime.execute(
                runtime.request_from_action({"tool": "export_file", "args": {"path": "secret.txt"}})
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.content, f"[EXPORT] {root / 'secret.txt'}")

            # Test export non-existent file
            result = runtime.execute(
                runtime.request_from_action({"tool": "export_file", "args": {"path": "missing.txt"}})
            )
            self.assertFalse(result.ok)
            self.assertIn("does not exist", result.content)

            # Test export outside workspace
            result = runtime.execute(
                runtime.request_from_action({"tool": "export_file", "args": {"path": "/etc/passwd"}})
            )
            self.assertFalse(result.ok)
            self.assertIn("outside workspace", result.content)

    def test_workspace_alias_resolves_to_workspace_root(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "project").mkdir()
            (root / "project" / "README.md").write_text("hello", encoding="utf-8")
            runner = WorkspaceToolRunner(root)

            self.assertEqual(runner.resolve_path("/workspace"), root)
            self.assertEqual(runner.resolve_path("/workspace/project"), root / "project")
            self.assertIn("README.md", runner.list_dir("/workspace/project"))

    def test_prepare_command_requires_confirmation_object(self):
        with TemporaryDirectory() as tempdir:
            runner = WorkspaceToolRunner(Path(tempdir))

            pending = runner.prepare_command("python --version")

            self.assertFalse(pending.background)
            self.assertIn("/confirm_tool", pending.confirm_text())

    def test_blocks_shell_operators_and_destructive_commands(self):
        with self.assertRaises(ToolError):
            validate_command("rm -rf dist")
        with self.assertRaises(ToolError):
            validate_command("npm test && npm run build")

    def test_cloud_escalation_requires_confirmation(self):
        with TemporaryDirectory() as tempdir:
            runtime = ToolRuntime(Path(tempdir))

            request = runtime.request_from_action(
                {"tool": "ask_gemini_cli", "args": {"prompt": "build project"}}
            )

        self.assertEqual(request.risk, "danger")

    def test_model_change_request_uses_callback(self):
        with TemporaryDirectory() as tempdir:
            runtime = ToolRuntime(
                Path(tempdir),
                request_gemini_model_change=lambda model: f"pending {model}",
            )

            request = runtime.request_from_action(
                {"tool": "request_gemini_model_change", "args": {"model": "gemini-test"}}
            )
            result = runtime.execute(request)

        self.assertEqual(request.risk, "medium")
        self.assertTrue(result.ok)
        self.assertIn("pending gemini-test", result.content)

    def test_make_dir_and_write_file(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            runtime = ToolRuntime(root)

            mkdir_result = runtime.execute(
                runtime.request_from_action(
                    {"tool": "make_dir", "args": {"path": "new_folder"}}
                )
            )
            write_result = runtime.execute(
                runtime.request_from_action(
                    {
                        "tool": "write_file",
                        "args": {"path": "new_folder/123.txt", "content": "hello"},
                    }
                )
            )
            content = (root / "new_folder" / "123.txt").read_text(encoding="utf-8")

        self.assertTrue(mkdir_result.ok)
        self.assertTrue(write_result.ok)
        self.assertEqual(content, "hello")

    def test_write_file_over_existing_file_requires_confirmation(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "exists.txt").write_text("old", encoding="utf-8")
            runtime = ToolRuntime(root)

            request = runtime.request_from_action(
                {"tool": "write_file", "args": {"path": "exists.txt", "content": "new"}}
            )

        self.assertEqual(request.risk, "danger")

    def test_blocked_shell_syntax_is_not_sent_for_confirmation(self):
        risk = classify_tool(
            "run_shell_safe",
            {"command": 'mkdir -p x && echo "hello" > x/123.txt'},
        )

        self.assertEqual(risk, "medium")

    def test_github_publish_commands_require_confirmation(self):
        for command in (
            "gh repo create ai-office-kernel --private",
            "git push -u origin main",
            "git commit -m publish",
            "git remote add origin git@github.com:user/repo.git",
        ):
            with self.subTest(command=command):
                risk = classify_tool("run_shell_safe", {"command": command})

                self.assertEqual(risk, "danger")

    def test_scan_secrets_redacts_findings(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env").write_text("TELEGRAM_BOT_TOKEN=123456789:SECRET_TOKEN\n", encoding="utf-8")
            runtime = ToolRuntime(root)

            result = runtime.execute(
                runtime.request_from_action({"tool": "scan_secrets", "args": {"path": "."}})
            )

        self.assertTrue(result.ok)
        self.assertIn("Potential secrets found", result.content)
        self.assertIn("REDACTED", result.content)
        self.assertNotIn("SECRET_TOKEN", result.content)

    def test_scan_secrets_ignores_common_code_false_positives(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / "src").mkdir()
            (root / "src" / "app.tsx").write_text(
                "<RepoCard key={repo.id} repo={repo} />\n",
                encoding="utf-8",
            )
            (root / "src" / "config.py").write_text(
                "\n".join(
                    [
                        "gemini_model=args.gemini_model",
                        "gemini_approval_mode=auto_edit",
                        "session=TaskSession(description='x')",
                        "api_parser=subparsers.add_parser('api')",
                        "COMPASS=abcdefghijklmnop",
                    ]
                ),
                encoding="utf-8",
            )
            runtime = ToolRuntime(root)

            result = runtime.execute(
                runtime.request_from_action({"tool": "scan_secrets", "args": {"path": "."}})
            )

        self.assertTrue(result.ok)
        self.assertIn("No likely secrets found", result.content)


if __name__ == "__main__":
    unittest.main()
