import unittest

from beecurious_service.providers import MockAgentProvider
from beecurious_service.sessions import SessionStore


class SessionStoreTest(unittest.TestCase):
    def test_game_start_returns_commands(self) -> None:
        session = SessionStore(MockAgentProvider()).create()

        response = session.handle_event(
            {"event": {"event_type": "game_start"}, "context": "Player is nearby."}
        )

        self.assertEqual(response["commands"][0]["type"], "say")
        self.assertEqual(response["commands"][1]["args"], ["player"])

    def test_accepts_legacy_uppercase_fabric_event(self) -> None:
        session = SessionStore(MockAgentProvider()).create()

        response = session.handle_event(
            {"event": {"EVENT_TYPE": "GAME_START"}, "context": ""}
        )

        self.assertEqual(response["commands"][0]["type"], "say")


if __name__ == "__main__":
    unittest.main()
