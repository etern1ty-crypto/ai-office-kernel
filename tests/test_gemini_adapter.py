import unittest

from ai_office_kernel.cli import CLIResult
from ai_office_kernel.cli.gemini import (
    AUTH_REQUIRED_MESSAGE,
    GeminiCLIAdapter,
    looks_like_auth_prompt,
    parse_json_result,
    parse_stream_json_result,
)


class GeminiAdapterTest(unittest.TestCase):
    def test_builds_headless_command_with_real_gemini_flags(self):
        adapter = GeminiCLIAdapter(
            command="gemini",
            system_prompt="Act as dev.",
            model="flash",
            output_format="stream-json",
            approval_mode="yolo",
            skip_trust=True,
            sandbox=True,
            include_directories=("src", "tests"),
            allowed_tools=("ShellTool(git status)",),
            resume="latest",
        )

        command = list(adapter.build_command("write parser"))

        self.assertEqual(command[0], "gemini")
        self.assertIn("--prompt", command)
        self.assertIn("Act as dev.\n\nTask:\nwrite parser", command)
        self.assertIn("--output-format", command)
        self.assertIn("stream-json", command)
        self.assertIn("--approval-mode", command)
        self.assertIn("yolo", command)
        self.assertIn("--skip-trust", command)
        self.assertIn("--sandbox", command)
        self.assertIn("src,tests", command)
        self.assertIn("--resume", command)
        self.assertIn("latest", command)

    def test_parses_json_output_usage(self):
        stdout = (
            '{"session_id":"s1","response":"done","stats":{"models":{'
            '"gemini-2.5-pro":{"tokens":{"prompt":10,"candidates":5,'
            '"total":15,"cached":2}}},"tools":{"totalCalls":1}}}'
        )
        result = parse_json_result(
            CLIResult(
                output=stdout,
                raw_output=stdout,
                stdout=stdout,
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1,
            )
        )

        self.assertEqual(result.output, "done")
        self.assertEqual(result.session_id, "s1")
        self.assertEqual(result.usage_summary(), "tokens=15 in=10 out=5 tools=1")

    def test_parses_stream_json_events(self):
        stdout = "\n".join(
            [
                '{"type":"init","timestamp":"t","session_id":"s1","model":"auto"}',
                '{"type":"message","timestamp":"t","role":"assistant","content":"hel","delta":true}',
                '{"type":"message","timestamp":"t","role":"assistant","content":"lo","delta":true}',
                '{"type":"tool_use","timestamp":"t","tool_name":"write_file","tool_id":"1","parameters":{}}',
                '{"type":"result","timestamp":"t","status":"success","stats":{"total_tokens":7,"input_tokens":4,"output_tokens":3,"cached":0,"input":4,"duration_ms":100,"tool_calls":1,"models":{}}}',
            ]
        )

        result = parse_stream_json_result(
            CLIResult(
                output=stdout,
                raw_output=stdout,
                stdout=stdout,
                exit_code=0,
                timed_out=False,
                duration_seconds=0.1,
            )
        )

        self.assertEqual(result.output, "hello")
        self.assertEqual(result.session_id, "s1")
        self.assertEqual(len(result.events), 5)
        self.assertEqual(result.usage_summary(), "tokens=7 in=4 out=3 tools=1")

    def test_detects_auth_prompt(self):
        self.assertTrue(
            looks_like_auth_prompt(
                "Opening authentication page in your browser. Do you want to continue?"
            )
        )
        self.assertIn("NO_BROWSER=1 gemini", AUTH_REQUIRED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
