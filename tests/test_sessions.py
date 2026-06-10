import unittest
from unittest.mock import patch

from beecurious_service.agents.registry import create_agent_registry
from beecurious_service.providers import MockAgentProvider
from beecurious_service.sessions import SessionStore
from beecurious_service.telemetry import LokiTelemetry


class _CapturingTelemetry:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


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
        self.assertTrue(response["commands"][0]["command_id"])

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

    @patch("beecurious_service.sessions.STATIONARY_SECONDS", 0)
    def test_bip_v3_speaks_once_after_stationary_heartbeat_history(self) -> None:
        store = SessionStore(
            MockAgentProvider(),
            create_agent_registry(),
            "bip",
            "3.0",
            LokiTelemetry("https://example.test", "test", None),
        )
        session = store.create(self._request())

        first = session.handle_event(self._heartbeat([1.0, 64.0, 2.0], 20))
        second = session.handle_event(self._heartbeat([1.0, 64.0, 2.0], 40))
        command_id = second["commands"][0]["command_id"]
        acknowledged = session.handle_event(
            self._heartbeat(
                [1.0, 64.0, 2.0],
                60,
                queued_command_ids=[command_id],
            )
        )

        self.assertEqual(first["commands"], [])
        self.assertEqual(second["commands"][0]["type"], "say")
        self.assertEqual(acknowledged["commands"], [])
        self.assertEqual(len(session.positions), 3)

    @patch("beecurious_service.sessions.STATIONARY_SECONDS", 0)
    def test_bip_v3_resets_after_player_moves(self) -> None:
        store = SessionStore(
            MockAgentProvider(),
            create_agent_registry(),
            "bip",
            "3.0",
            LokiTelemetry("https://example.test", "test", None),
        )
        session = store.create(self._request())

        session.handle_event(self._heartbeat([1.0, 64.0, 2.0], 20))
        stationary = session.handle_event(self._heartbeat([1.0, 64.0, 2.0], 40))
        moved = session.handle_event(
            self._heartbeat(
                [4.0, 64.0, 2.0],
                60,
                current_command_id=stationary["commands"][0]["command_id"],
            )
        )

        self.assertEqual(stationary["commands"][0]["type"], "say")
        self.assertEqual(moved["commands"], [])
        self.assertFalse(session.stationary_alerted)

    def test_heartbeat_resends_only_unacknowledged_commands(self) -> None:
        session = self.store.create(self._request())
        generated = session.handle_event(
            {"event": {"event_type": "game_start"}, "context": ""}
        )
        command_ids = [
            command["command_id"] for command in generated["commands"]
        ]

        retry = session.handle_event(self._heartbeat([0, 64, 0], 20))
        acknowledged = session.handle_event(
            self._heartbeat(
                [0, 64, 0],
                40,
                current_command_id=command_ids[0],
                queued_command_ids=[command_ids[1]],
            )
        )

        self.assertEqual(
            [command["command_id"] for command in retry["commands"]],
            command_ids,
        )
        self.assertEqual(acknowledged["commands"], [])

    def test_heartbeat_tracks_completed_and_failed_commands(self) -> None:
        session = self.store.create(self._request())
        generated = session.handle_event(
            {"event": {"event_type": "game_start"}, "context": ""}
        )
        command_ids = [
            command["command_id"] for command in generated["commands"]
        ]

        session.handle_event(
            self._heartbeat(
                [0, 64, 0],
                20,
                completed_command_ids=[command_ids[0]],
                failed_command_ids=[command_ids[1]],
            )
        )

        self.assertEqual(session.commands[command_ids[0]].state, "completed")
        self.assertEqual(session.commands[command_ids[1]].state, "failed")

    def test_heartbeat_logs_game_state_without_calling_provider(self) -> None:
        telemetry = _CapturingTelemetry()
        store = SessionStore(
            MockAgentProvider(),
            create_agent_registry(),
            "bip",
            "1.0",
            telemetry,
        )
        session = store.create(self._request(logging_consent=True))
        telemetry.events.clear()

        with patch.object(session.provider, "generate") as generate:
            response = session.handle_event(self._heartbeat([0, 64, 0], 20))

        self.assertEqual(response["commands"], [])
        generate.assert_not_called()
        self.assertEqual(len(telemetry.events), 1)
        event = telemetry.events[0]
        self.assertEqual(event.event_type, "game_state")
        self.assertEqual(event.data["snapshot"]["game_tick"], 20)
        self.assertEqual(event.data["snapshot"]["player"]["position"], [0, 64, 0])
        self.assertEqual(event.data["execution"]["queued_command_ids"], [])
        self.assertEqual(event.data["agent_profile"], "bip@1.0")

    @staticmethod
    def _heartbeat(
        position: list[float | int],
        game_tick: int,
        **execution_overrides: object,
    ) -> dict[str, object]:
        execution: dict[str, object] = {
            "current_command_id": None,
            "queued_command_ids": [],
            "completed_command_ids": [],
            "failed_command_ids": [],
        }
        execution.update(execution_overrides)
        return {
            "event": {
                "event_type": "agent_tick",
                "snapshot": {
                    "game_tick": game_tick,
                    "player": {"position": position},
                    "agent": {"position": [0.5, 65.0, 0.5]},
                    "flowers": {"count": 20, "items": []},
                },
                "execution": execution,
            }
        }

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
