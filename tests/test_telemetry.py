import json
import unittest
from unittest.mock import patch

from beecurious_service.telemetry import (
    LokiTelemetry,
    PermanentLokiError,
    TelemetryEvent,
    USER_AGENT,
)


class _Response:
    status = 204

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class LokiTelemetryTest(unittest.TestCase):
    def test_disabled_without_password(self) -> None:
        telemetry = LokiTelemetry("https://example.test", "test", None)

        self.assertFalse(telemetry.enabled)

    @patch("beecurious_service.telemetry.request.urlopen")
    def test_structured_payload_keeps_session_ids_separate(self, urlopen: object) -> None:
        urlopen.return_value = _Response()
        telemetry = LokiTelemetry("https://example.test", "test", "secret")
        event = TelemetryEvent(
            event_type="agent_commands_generated",
            game_session_id="game-1",
            agent_session_id="agent-2",
            interaction_id="interaction-3",
            data={"commands": [{"type": "say", "args": ["hello"]}]},
        )

        telemetry._send(event)

        http_request = urlopen.call_args.args[0]
        loki_payload = json.loads(http_request.data.decode("utf-8"))
        record = json.loads(loki_payload["streams"][0]["values"][0][1])
        self.assertEqual(http_request.get_header("User-agent"), USER_AGENT)
        self.assertEqual(record["game_session_id"], "game-1")
        self.assertEqual(record["agent_session_id"], "agent-2")
        self.assertEqual(record["interaction_id"], "interaction-3")
        self.assertNotIn("game_session_id", loki_payload["streams"][0]["stream"])

    @patch.object(LokiTelemetry, "_send")
    def test_permanent_http_failure_is_not_retried(self, send: object) -> None:
        send.side_effect = PermanentLokiError("Loki returned HTTP 403")
        telemetry = LokiTelemetry("https://example.test", "test", None)

        telemetry._send_with_retry(
            TelemetryEvent(
                event_type="agent_session_created",
                game_session_id="game-1",
            )
        )

        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
