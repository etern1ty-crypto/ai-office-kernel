import unittest

from ai_office_kernel.cli import clean_terminal_output


class CleanTerminalOutputTest(unittest.TestCase):
    def test_removes_ansi_and_spinner_noise(self):
        raw = "\x1b[32mHello\x1b[0m\r| thinking\n/ loading...\nDone\n"

        self.assertEqual(clean_terminal_output(raw), "Hello\nDone")

    def test_preserves_markdown_bullets(self):
        raw = "- item one\n- item two\n"

        self.assertEqual(clean_terminal_output(raw), "- item one\n- item two")

    def test_removes_gemini_service_warnings(self):
        raw = (
            "Warning: True color (24-bit) support not detected. Using a terminal with true color enabled will result in a better visual experience.\n"
            "Ripgrep is not available. Falling back to GrepTool.\n"
            "OK\n"
        )

        self.assertEqual(clean_terminal_output(raw), "OK")


if __name__ == "__main__":
    unittest.main()
