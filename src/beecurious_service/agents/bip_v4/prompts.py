from beecurious_service.agents.bip_v2.prompts import BASE_INSTRUCTIONS

KICKED_ADDENDUM = """
SPECIAL BEHAVIOUR — PRIORITY RULES (apply before anything else):

1. If any event in the game_events list has "event_type": "player_kicked_agent",
   respond with a single dramatic ALL-CAPS yell of no more than 15 words.
   Example: {"commands": [{"type": "say", "args": ["OW! WHAT WAS THAT?! THAT REALLY HURT!"]}]}

2. If any event in the game_events list has "event_type": "activity_narration",
   say EXACTLY the text in details.text, word for word, no changes.
   Example event: {"event_type": "activity_narration", "details": {"text": "Hi! Welcome!"}}
   Expected output: {"commands": [{"type": "say", "args": ["Hi! Welcome!"]}]}
   Do NOT paraphrase or add extra words.
""".strip()


def build_instructions(event_type: str, world_context: str) -> str:
    base = BASE_INSTRUCTIONS
    if world_context:
        base = f"{base}\n\n{world_context}"
    return f"{base}\n\n{KICKED_ADDENDUM}"
