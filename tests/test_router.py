import unittest

from ai_office_kernel.router import Router


class RouterTest(unittest.TestCase):
    def test_explicit_dev_trigger(self):
        decision = Router().route("@dev write a parser")

        self.assertEqual(decision.role_id, "developer")
        self.assertEqual(decision.task_text, "write a parser")
        self.assertTrue(decision.run_qa)

    def test_command_trigger(self):
        decision = Router().route("/qa review this code")

        self.assertEqual(decision.role_id, "qa")
        self.assertEqual(decision.task_text, "review this code")

    def test_code_keyword_routes_to_developer(self):
        decision = Router().route("Нужен парсер логов")

        self.assertEqual(decision.role_id, "developer")

    def test_default_routes_to_secretary(self):
        decision = Router().route("Какие планы на завтра?")

        self.assertEqual(decision.role_id, "secretary")


if __name__ == "__main__":
    unittest.main()

