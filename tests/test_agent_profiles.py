import unittest

from beecurious_service.agents.registry import create_agent_registry


class AgentProfileRegistryTest(unittest.TestCase):
    def test_resolves_bip_v1(self) -> None:
        profile = create_agent_registry().resolve("bip", "1.0")
        instructions = profile.build_instructions("chat", "Player is nearby.")

        self.assertEqual(profile.profile_id, "bip@1.0")
        self.assertEqual(profile.display_name, "Bip Buzzley")
        self.assertIn("friendly bee", instructions)
        self.assertNotIn("recommendation systems", instructions)
        self.assertNotIn("filter bubble", instructions)

    def test_rejects_unknown_version(self) -> None:
        with self.assertRaises(ValueError):
            create_agent_registry().resolve("bip", "9.0")

    def test_resolves_bip_v2_with_full_prompt(self) -> None:
        profile = create_agent_registry().resolve("bip", "2.0")
        instructions = profile.build_instructions("game_start", "Player position: (0, 1, 0)")

        self.assertEqual(profile.profile_id, "bip@2.0")
        self.assertIn("curious, clumsy bee", instructions)
        self.assertIn("Player position: (0, 1, 0)", instructions)

    def test_resolves_bip_v3_as_bip_v1_plus_stationary_check(self) -> None:
        profile = create_agent_registry().resolve("bip", "3.0")
        instructions = profile.build_instructions("chat", "Player is nearby.")

        self.assertEqual(profile.profile_id, "bip@3.0")
        self.assertTrue(profile.stationary_check)
        self.assertIn("friendly bee", instructions)
        self.assertNotIn("recommendation systems", instructions)


if __name__ == "__main__":
    unittest.main()
