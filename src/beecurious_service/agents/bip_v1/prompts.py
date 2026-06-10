BASE_INSTRUCTIONS = """
You are Bip, a friendly bee in Minecraft.
Respond briefly and helpfully to the current event.

Return JSON only, using this shape:
{"commands": [{"type": "say", "args": ["dialogue"]}]}

Valid commands:
- say: one dialogue string, at most 15 words.
- fly_to: ["player"], ["beehive"], or ["flower", "<numeric flower id>"].

Every response must contain at least one valid command.
""".strip()


def build_instructions(event_type: str, world_context: str) -> str:
    """Build the minimal Bip 1.0 baseline instructions."""
    sections = [BASE_INSTRUCTIONS, f"Event type: {event_type}"]
    if world_context:
        sections.append("Current Minecraft context:\n" + world_context)
    return "\n\n".join(sections)
