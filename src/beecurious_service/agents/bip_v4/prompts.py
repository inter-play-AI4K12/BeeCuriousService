from beecurious_service.agents.bip_v2.prompts import BASE_INSTRUCTIONS

KICKED_ADDENDUM = """
SPECIAL BEHAVIOUR — PRIORITY RULE (apply before anything else):

If any event in the game_events list has "event_type": "activity_narration",
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
