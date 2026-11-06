import re
import unittest
from dataclasses import replace

from beecurious_service import activities
from beecurious_service.agents.registry import create_agent_registry
from beecurious_service.features import BipFeatures


ALL_ON = BipFeatures(
    brevity=True,
    stay_on_task=True,
    llm_improv=True,
    tiered_hints=True,
    interactive_qa=True,
    time_travel_handover=True,
    scripted_memory=True,
)

QUIZ_IDS = [
    "user_profile", "flower_buds_on_ground", "numbers_above_flower_buds", "pollen_circle",
    "flowers_located_in_garden",
]


def _asks(topic: str) -> list[str]:
    return [f"{topic}_ask", f"{topic}_nudge_0", f"{topic}_nudge_1"]


# Every beat the Java activity states emit, per activity. Keep in sync with the mod.
JAVA_BEATS = {
    "intro": ["hello", "ask_name", "ask_name_nudge_0", "ask_name_nudge_1", "purpose_garden",
              "purpose_apps", "purpose_goal", "plan", "questions", "go"],
    "observe": ["hello", "hello_nudge_0", "hello_nudge_1", "welcome", "show_flower", "point_at",
                "point_nudge_0", "point_nudge_1", "panel_found", "panel_color", "panel_smell",
                "panel_nectar", "panel_water", "panel_sun", "panel_number", "panel_meaning",
                "explore_ask", "explore_suggest_0", "explore_suggest_1", "explore_different",
                "different_suggest_0", "different_suggest_1", "questions",
                "questions_more", "wrap", "next_tease"],
    "pollinate": ["hello", "hello_nudge_0", "hello_nudge_1", "welcome", "recap", "teach_point",
                  "teach_point_nudge_0", "teach_point_nudge_1", "teach_slot", "teach_click",
                  "pollinate_reminder_0", "pollinate_reminder_1", "first_pollinated",
                  "hive_moved", "buds", "numbers", "circle", "pollen", "new_flowers", "wither",
                  *_asks("why_dead"), "why_dead_hint", "diversity_intro", "diversity_meaning",
                  "diversity_dropped", "clock_handoff", "try_clocks", "again", "again_pick",
                  "buds_again", "new_flowers_again", "diversity_check", "keep_going_0",
                  "keep_going_1", "time_travel", "diversity_dropped_again", "garden_died",
                  "diversity_end", "bridge", "lets_go"],
    "recommend": ["intro", "intro_returning", "find_hive", "find_hive_nudge_0",
                  "find_hive_nudge_1", *_asks("distance"), "distance_explain", "go_pollinate",
                  "pollinate_reminder_0", "pollinate_reminder_1", *_asks("numbers"),
                  "numbers_explain", *_asks("hive"), "hive_explain", "clock_handoff",
                  *_asks("circle"), "circle_explain", *_asks("wither"), "wither_explain",
                  "new_flowers", "time_travel", "diversity_dropped_again", "quiz_intro",
                  "quiz_right_0", "quiz_right_1", "quiz_right_2", *_asks("wrap"), "wrap_reveal",
                  "wrap_recommender", "wrap_bubble", "complete",
                  *[f"quiz_q_{q}" for q in QUIZ_IDS], *[f"quiz_wrong_{q}" for q in QUIZ_IDS]],
    "diversify": ["hello", "hello_nudge_0", "hello_nudge_1", "welcome", "look", "recall", "goal",
                  "goal_bar", "hint_circle", "go", "pollinate_reminder_0", "pollinate_reminder_1",
                  "press_b", "press_b_nudge_0", "press_b_nudge_1", "circle_grew",
                  "lever_appeared", "lever_ask", "lever_nudge_0", "lever_nudge_1", "lever_on",
                  "lever_off", "bloom_check", "why_ask", "why_nudge_0", "why_nudge_1",
                  "why_explain", "keep_going_0", "keep_going_1", "almost", "time_travel",
                  "diversity_dropped_again", "success", "lesson", "complete"],
    "actual": ["welcome", "goal", "help", "round_react", "time_travel",
               "diversity_dropped_again"],
    "flow": ["portal_open", "portal_nudge", "tutorial_done", "next_stage_item"],
}


def _spoken_lines():
    for activity, table in activities.ACTIVITIES.items():
        for name, beat in table.items():
            for line in (beat.say_before, beat.say):
                if line:
                    yield f"{activity}/{name}", line


class ActivityTablesTest(unittest.TestCase):
    def test_every_beat_the_mod_emits_resolves(self) -> None:
        for activity, beats in JAVA_BEATS.items():
            for name in beats:
                with self.subTest(activity=activity, beat=name):
                    beat = activities.resolve(activity, name, ALL_ON)
                    self.assertIsNotNone(beat)
                    self.assertTrue(beat.say)

    def test_clock_handoff_respects_feature_flag(self) -> None:
        features = replace(ALL_ON, time_travel_handover=False)
        for activity in ("pollinate", "recommend"):
            with self.subTest(activity=activity):
                self.assertIsNone(activities.resolve(activity, "clock_handoff", features))
                self.assertTrue(activities.resolve(activity, "clock_handoff", ALL_ON).give_clocks)

    def test_beats_fly_to_their_subject(self) -> None:
        self.assertEqual(activities.resolve("recommend", "find_hive", ALL_ON).fly_to,
                         ("beehive",))
        self.assertEqual(activities.resolve("observe", "show_flower", ALL_ON).fly_to,
                         ("flower", "@id"))
        self.assertEqual(activities.resolve("pollinate", "wither", ALL_ON).fly_to,
                         ("flower", "@id"))
        self.assertEqual(activities.resolve("diversify", "lever_appeared", ALL_ON).fly_to,
                         ("lever",))
        self.assertEqual(activities.resolve("flow", "portal_open", ALL_ON).fly_to, ("portal",))

    def test_placeholders_present(self) -> None:
        self.assertIn("{color}", activities.resolve("observe", "panel_color", ALL_ON).say)
        self.assertIn("{trait_word}",
                      activities.resolve("observe", "explore_suggest_0", ALL_ON).say)
        self.assertIn("{target}", activities.resolve("diversify", "goal", ALL_ON).say)

    def test_improv_beats_keep_a_scripted_fallback(self) -> None:
        for activity, table in activities.ACTIVITIES.items():
            for name, beat in table.items():
                if beat.improv:
                    with self.subTest(activity=activity, beat=name):
                        self.assertTrue(beat.say)


class NaturalToneTest(unittest.TestCase):
    def test_spoken_lines_follow_hard_tone_rules(self) -> None:
        for where, line in _spoken_lines():
            with self.subTest(beat=where, line=line):
                self.assertNotIn("—", line)
                self.assertNotIn(";", line)
                self.assertNotIn("buzz", line.lower().replace("buzzley", ""))
                self.assertLessEqual(line.count("!"), 1)
                words = re.sub(r"\{[a-z_]+\}", "X", line).split()
                self.assertLessEqual(len(words), 16)

    def test_spoken_lines_avoid_adult_phrasing(self) -> None:
        banned = ["let's pause", "take a closer look", "varied", "come on"]
        for where, line in _spoken_lines():
            for phrase in banned:
                with self.subTest(beat=where, phrase=phrase):
                    self.assertNotIn(phrase, line.lower())


class BipV4StanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = create_agent_registry().resolve("bip", "4.0")

    def test_v4_is_activity_aware(self) -> None:
        self.assertTrue(self.profile.activity_aware)

    def test_teacher_buddy_before_recommend(self) -> None:
        for activity in (None, "intro", "observe", "pollinate"):
            with self.subTest(activity=activity):
                text = self.profile.build_instructions("chat", "", activity=activity)
                self.assertIn("TEACHER BUDDY", text)
                self.assertIn("SAVE THE BIG WHY", text)
                self.assertNotIn("GUIDE MODE", text)

    def test_stance_per_later_activity(self) -> None:
        expected = {"recommend": "GUIDE MODE", "diversify": "BUBBLE BREAKER",
                    "actual": "GARDEN BUDDY"}
        for activity, marker in expected.items():
            with self.subTest(activity=activity):
                text = self.profile.build_instructions("chat", "", activity=activity)
                self.assertIn(marker, text)
                self.assertNotIn("TEACHER BUDDY", text)

    def test_every_stance_gets_knowledge_tone_and_sight(self) -> None:
        for activity in (None, "observe", "recommend", "diversify", "actual"):
            with self.subTest(activity=activity):
                text = self.profile.build_instructions("chat", "", activity=activity)
                self.assertIn("HOW THE GARDEN WORKS", text)
                self.assertIn("NATURAL TONE", text)
                self.assertIn("YOU CAN SEE EVERYTHING", text)

    def test_older_profiles_keep_two_argument_builder(self) -> None:
        for version in ("1.0", "2.0", "3.0"):
            with self.subTest(version=version):
                profile = create_agent_registry().resolve("bip", version)
                self.assertFalse(profile.activity_aware)
                self.assertTrue(profile.build_instructions("chat", ""))


if __name__ == "__main__":
    unittest.main()
