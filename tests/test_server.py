import unittest

from beecurious_service.server import BeeCuriousRequestHandler


class RequestHandlerHelpersTest(unittest.TestCase):
    def test_logs_path_uses_agent_session_id(self) -> None:
        self.assertEqual(
            BeeCuriousRequestHandler._logs_session_id(
                "/v1/sessions/agent-123/logs"
            ),
            "agent-123",
        )
        self.assertIsNone(
            BeeCuriousRequestHandler._logs_session_id(
                "/v1/game-sessions/game-123/logs"
            )
        )

    def test_log_filters_parse_supported_values(self) -> None:
        filters = BeeCuriousRequestHandler._log_filters(
            {
                "event_type": ["AGENT_COMMANDS_GENERATED"],
                "interaction_id": ["interaction-3"],
                "hours": ["12"],
                "limit": ["25"],
            }
        )

        self.assertEqual(filters["event_type"], "agent_commands_generated")
        self.assertEqual(filters["interaction_id"], "interaction-3")
        self.assertEqual(filters["hours"], 12)
        self.assertEqual(filters["limit"], 25)

    def test_log_filters_reject_out_of_range_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "limit must be between"):
            BeeCuriousRequestHandler._log_filters({"limit": ["1001"]})


if __name__ == "__main__":
    unittest.main()
