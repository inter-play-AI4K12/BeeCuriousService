import unittest

from beecurious_service.models import validate_commands


class ValidateCommandsTest(unittest.TestCase):
    def test_accepts_supported_commands(self) -> None:
        commands = validate_commands(
            [
                {"type": "say", "args": ["Hello!"]},
                {"type": "fly_to", "args": ["flower", "22"]},
            ]
        )

        self.assertEqual(commands[0].type, "say")
        self.assertEqual(commands[1].args, ["flower", "22"])

    def test_rejects_response_without_valid_commands(self) -> None:
        with self.assertRaises(ValueError):
            validate_commands([{"type": "teleport", "args": []}])


if __name__ == "__main__":
    unittest.main()
