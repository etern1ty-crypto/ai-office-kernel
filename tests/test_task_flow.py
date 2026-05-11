import unittest

from ai_office_kernel.task_flow import TaskSession


class TaskFlowTest(unittest.TestCase):
    def test_task_session_collects_notes_and_prompt(self):
        session = TaskSession(description="Сделать парсер логов", gemini_model="gemini-2.5-flash")

        session.add_note("Формат nginx")
        session.add_note("Нужны тесты")

        prompt = session.full_prompt()
        self.assertIn("Сделать парсер логов", prompt)
        self.assertIn("- Формат nginx", prompt)
        self.assertIn("- Нужны тесты", prompt)

    def test_execution_prompt_turns_notes_into_brief_request(self):
        session = TaskSession(description="Сделать сайт", gemini_model="auto")
        session.add_note("GitHub etern1ty-crypto")

        prompt = session.execution_prompt()

        self.assertIn("technical brief", prompt)
        self.assertIn("GitHub etern1ty-crypto", prompt)

    def test_model_change_requires_confirmation(self):
        session = TaskSession(description="task", gemini_model="gemini-2.5-flash")

        session.request_model_change("gemini-2.5-pro")

        self.assertEqual(session.gemini_model, "gemini-2.5-flash")
        self.assertEqual(session.confirm_model_change(), "gemini-2.5-pro")
        self.assertEqual(session.gemini_model, "gemini-2.5-pro")

    def test_run_requires_confirmation(self):
        session = TaskSession(description="task", gemini_model="gemini-2.5-flash")

        self.assertFalse(session.consume_run_confirmation())
        session.request_run()

        self.assertTrue(session.consume_run_confirmation())
        self.assertFalse(session.consume_run_confirmation())


if __name__ == "__main__":
    unittest.main()
