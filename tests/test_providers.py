import json
import unittest
from unittest.mock import patch

from beecurious_service.config import Settings
from beecurious_service.providers import RochesterAgentProvider


class _Response:
    def __init__(self, payload: dict[str, object]):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class RochesterAgentProviderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            host="127.0.0.1",
            port=8765,
            provider="mock",
            model="test",
            default_agent_id="bip",
            default_agent_version="4.0",
            openai_api_key=None,
            openai_base_url="https://openai.example/v1",
            openai_org_id=None,
            openai_project_id=None,
            rochester_api_key="test-key",
            rochester_base_url="https://rochester.example",
            rochester_model="astro_next",
            loki_url="https://loki.example",
            loki_username="test",
            loki_password=None,
        )

    @patch("beecurious_service.providers.request.urlopen")
    def test_initializes_conversation_and_returns_reference(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "response": (
                    '{"commands":[{"type":"say","args":["Hello!"]}]}'
                ),
                "reference": "reference-1",
            }
        )

        result = RochesterAgentProvider(self.settings).generate(
            "Bip instructions",
            {"event_type": "game_start"},
            None,
        )

        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://rochester.example/init")
        self.assertEqual(sent["model"], "astro_next")
        self.assertEqual(result.response_id, "reference-1")
        self.assertEqual(result.commands[0].args, ["Hello!"])

    @patch("beecurious_service.providers.request.urlopen")
    def test_steps_existing_conversation(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "response": (
                    "```json\n"
                    '{"commands":[{"type":"fly_to","args":["player"]}]}\n'
                    "```"
                ),
                "reference": "reference-2",
            }
        )

        result = RochesterAgentProvider(self.settings).generate(
            "Updated instructions",
            {"event_type": "chat", "message": "Come here"},
            "reference-1",
        )

        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://rochester.example/step")
        self.assertEqual(sent["reference"], "reference-1")
        self.assertNotIn("Updated instructions", sent["message"])
        self.assertIn('"event_type": "chat"', sent["message"])
        self.assertEqual(result.response_id, "reference-2")
        self.assertEqual(result.commands[0].args, ["player"])

    @patch("beecurious_service.providers.request.urlopen")
    def test_wraps_natural_language_as_a_short_say_command(self, urlopen) -> None:
        urlopen.return_value = _Response(
            {
                "response": (
                    "What evidence in the garden supports your idea, and "
                    "what might another player notice differently before "
                    "deciding which explanation is strongest?"
                ),
                "reference": "reference-3",
            }
        )

        result = RochesterAgentProvider(self.settings).generate(
            "Bip instructions",
            {"event_type": "chat"},
            None,
        )

        dialogue = result.commands[0].args[0]
        self.assertEqual(result.commands[0].type, "say")
        self.assertLessEqual(len(dialogue.split()), 15)
        self.assertTrue(dialogue.endswith("..."))


if __name__ == "__main__":
    unittest.main()
