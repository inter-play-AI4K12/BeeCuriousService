from beecurious_service.agents.base import AgentProfile
from beecurious_service.agents.bip_v1.prompts import build_instructions


BIP_V4_PROFILE = AgentProfile(
    agent_id="bip",
    version="4.0",
    display_name="Bip Buzzley",
    build_instructions=build_instructions,
    provider_id="rochester",
)
