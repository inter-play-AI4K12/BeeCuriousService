import unittest

from beecurious_service.agents.registry import create_agent_registry
from beecurious_service.providers import MockAgentProvider
from beecurious_service.sessions import SessionStore
from beecurious_service.telemetry import LokiTelemetry


class SessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SessionStore(
            MockAgentProvider(),
            create_agent_registry(),
            "bip",
            "1.0",
            LokiTelemetry("https://example.test", "test", None),
        )

    def test_game_start_returns_commands(self) -> None:
        session = self.store.create(self._request())

        response = session.handle_event(
            {"event": {"event_type": "game_start"}, "context": "Player is nearby."}
        )

        self.assertEqual(response["commands"][0]["type"], "say")
        self.assertEqual(response["commands"][1]["args"], ["player"])

    def test_accepts_legacy_uppercase_fabric_event(self) -> None:
        session = self.store.create(self._request())

        response = session.handle_event(
            {"event": {"EVENT_TYPE": "GAME_START"}, "context": ""}
        )

        self.assertEqual(response["commands"][0]["type"], "say")

    def test_pins_default_profile_to_session(self) -> None:
        session = self.store.create(self._request())

        self.assertEqual(session.profile.profile_id, "bip@1.0")
        self.assertEqual(session.game_session_id, "game-test")

    def test_accepts_explicit_profile_override(self) -> None:
        session = self.store.create(
            self._request(agent="bip", version="1.0")
        )

        self.assertEqual(session.profile.profile_id, "bip@1.0")

    def test_rejects_unknown_profile_override(self) -> None:
        with self.assertRaises(ValueError):
            self.store.create(self._request(agent="bip", version="9.0"))

    def test_requires_game_session_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "game_session_id"):
            self.store.create({})

    def test_returns_interaction_id(self) -> None:
        session = self.store.create(self._request())

        response = session.handle_event(
            {"event": {"event_type": "game_start"}, "context": ""}
        )

        self.assertTrue(response["interaction_id"])

    @staticmethod
    def _request(**overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "game_session_id": "game-test",
            "logging_consent": False,
        }
        request.update(overrides)
        return request


if __name__ == "__main__":
    unittest.main()
