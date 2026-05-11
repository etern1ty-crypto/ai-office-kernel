import unittest

from ai_office_kernel.memory import ChatMessage, SharedMemory


class SharedMemoryTest(unittest.TestCase):
    def test_keeps_ring_buffer_per_chat(self):
        memory = SharedMemory(max_messages=2)
        memory.add(1, ChatMessage(role="user", content="one"))
        memory.add(1, ChatMessage(role="assistant", content="two"))
        memory.add(1, ChatMessage(role="user", content="three"))

        self.assertEqual(
            [message.content for message in memory.recent(1)],
            ["two", "three"],
        )

    def test_context_text(self):
        memory = SharedMemory(max_messages=3)
        memory.add(1, ChatMessage(role="user", content="hello"))

        self.assertEqual(memory.context_text(1), "user: hello")


if __name__ == "__main__":
    unittest.main()

