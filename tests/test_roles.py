import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_office_kernel.roles import DEFAULT_ROLES, role_prompt


class RolesTest(unittest.TestCase):
    def test_role_prompt_uses_prompt_dir_override(self):
        with TemporaryDirectory() as tempdir:
            prompt_dir = Path(tempdir)
            (prompt_dir / "secretary.md").write_text("custom secretary", encoding="utf-8")

            self.assertEqual(role_prompt("secretary", prompt_dir), "custom secretary")

    def test_role_prompt_falls_back_to_default(self):
        with TemporaryDirectory() as tempdir:
            prompt_dir = Path(tempdir)

            self.assertEqual(
                role_prompt("developer", prompt_dir),
                DEFAULT_ROLES["developer"].system_prompt,
            )


if __name__ == "__main__":
    unittest.main()
